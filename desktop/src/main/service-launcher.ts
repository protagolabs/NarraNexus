/**
 * @file service-launcher.ts
 * @description Phase 3: Service startup with Docker 500 retry logic
 *
 * Launch steps: wait-docker → compose-up → wait-mysql → init-tables → wait-evermemos → start-services
 * Extracted from process-manager.ts runQuickStart + runAutoSetup Step 6-12.
 *
 * Core fix: waits for Docker daemon to become healthy before running compose,
 * preventing the 500 error → privileges escalation misdiagnosis.
 */

import { spawn, execFile } from 'child_process'
import { join } from 'path'
import { existsSync } from 'fs'
import { promisify } from 'util'
import { EventEmitter } from 'events'
import * as net from 'net'
import {
  PROJECT_ROOT,
  TABLE_MGMT_DIR,
  EVERMEMOS_DIR
} from './constants'
import {
  detectDockerState,
  waitForDockerReady,
  ensureDockerDaemon,
  isEverMemOSAvailable,
  startEverMemOS
} from './docker-manager'
import { getShellEnv } from './shell-env'
import { readEnv } from './env-manager'
import type { ProcessManager } from './process-manager'
import type { LaunchStep, LaunchStepId } from '../shared/setup-types'

const execFileAsync = promisify(execFile)

// ─── Utilities ───────────────────────────────────────

function getExecEnv(): Record<string, string> {
  const shellEnv = getShellEnv()
  const dotEnv = readEnv()
  const nonEmptyDotEnv: Record<string, string> = {}
  for (const [key, value] of Object.entries(dotEnv)) {
    if (value.trim()) nonEmptyDotEnv[key] = value
  }
  const noProxyHosts = 'localhost,127.0.0.1'
  const merged = { ...shellEnv, ...nonEmptyDotEnv, NO_PROXY: noProxyHosts, no_proxy: noProxyHosts }

  // On macOS, ensure Docker Desktop bin is first in PATH — on Intel Mac, Homebrew's
  // /usr/local/bin/docker is a CLI-only binary without compose plugin.
  if (process.platform === 'darwin') {
    const ddBin = '/Applications/Docker.app/Contents/Resources/bin'
    const currentPath = merged.PATH || ''
    if (!currentPath.startsWith(ddBin)) {
      // Remove existing entry (if any) and prepend
      const parts = currentPath.split(':').filter(p => p !== ddBin)
      merged.PATH = [ddBin, ...parts].join(':')
    }
  }

  return merged
}

async function execInProject(
  cmd: string,
  args: string[],
  options?: { cwd?: string; timeout?: number }
): Promise<{ stdout: string; stderr: string }> {
  return execFileAsync(cmd, args, {
    cwd: options?.cwd ?? PROJECT_ROOT,
    timeout: options?.timeout ?? 120000,
    env: getExecEnv()
  })
}

async function execWithPrivileges(
  script: string,
  options?: { timeout?: number }
): Promise<{ stdout: string; stderr: string }> {
  if (process.platform === 'darwin') {
    // Docker Desktop bin MUST come first — on Intel Mac, Homebrew's /usr/local/bin/docker
    // is a CLI-only binary without compose plugin; Docker Desktop's docker has compose built in.
    const extraPaths = [
      '/Applications/Docker.app/Contents/Resources/bin',
      '/usr/local/bin',
      '/opt/homebrew/bin',
    ].join(':')
    // Set HOME so root shell can find user's ~/.docker/cli-plugins/
    const home = process.env.HOME || ''
    const fullScript = `export PATH="${extraPaths}:$PATH" && export HOME="${home}" && ${script}`
    const escaped = fullScript.replace(/\\/g, '\\\\').replace(/"/g, '\\"')
    return execInProject('osascript', ['-e',
      `do shell script "${escaped}" with administrator privileges`
    ], options)
  } else {
    return execInProject('pkexec', ['sh', '-c', script], options)
  }
}

function spawnWithOutput(
  cmd: string,
  args: string[],
  options: { cwd?: string; timeout?: number; onOutput: (line: string) => void }
): Promise<void> {
  return new Promise((resolve, reject) => {
    const proc = spawn(cmd, args, {
      cwd: options.cwd ?? PROJECT_ROOT,
      env: getExecEnv(),
      stdio: ['ignore', 'pipe', 'pipe']
    })
    const processData = (data: Buffer) => {
      const lines = data.toString().split('\n').filter(l => l.trim())
      for (const line of lines) {
        console.log(`[launcher] ${line}`)
      }
      if (lines.length > 0) {
        options.onOutput(lines[lines.length - 1].trim().substring(0, 200))
      }
    }
    proc.stdout?.on('data', processData)
    proc.stderr?.on('data', processData)

    const timer = setTimeout(() => {
      proc.kill('SIGTERM')
      reject(new Error(`Command timed out after ${(options.timeout ?? 120000) / 1000}s`))
    }, options.timeout ?? 120000)

    proc.on('close', (code) => {
      clearTimeout(timer)
      if (code === 0) resolve()
      else reject(new Error(`Process exited with code ${code}`))
    })
    proc.on('error', (err) => { clearTimeout(timer); reject(err) })
  })
}

function isPortReachable(port: number, host = '127.0.0.1', timeout = 2000): Promise<boolean> {
  return new Promise((resolve) => {
    const socket = new net.Socket()
    socket.setTimeout(timeout)
    socket.on('connect', () => { socket.destroy(); resolve(true) })
    socket.on('error', () => resolve(false))
    socket.on('timeout', () => { socket.destroy(); resolve(false) })
    socket.connect(port, host)
  })
}

function delay(ms: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms))
}

// ─── EverMemOS Infrastructure Ports ───────────────────

const EM_INFRA_PORTS = [
  { port: 27017, name: 'MongoDB' },
  { port: 19200, name: 'Elasticsearch' },
  { port: 19530, name: 'Milvus' },
  { port: 6379,  name: 'Redis' }
]

// ─── Service Launcher ───────────────────────────────

export class ServiceLauncher extends EventEmitter {
  private processManager: ProcessManager

  constructor(processManager: ProcessManager) {
    super()
    this.processManager = processManager
  }

  /**
   * Launch all services (Phase 3)
   * Emits 'launch-step' events for each step progress update.
   */
  async launch(options?: { skipEverMemOS?: boolean }): Promise<{ success: boolean; error?: string }> {
    let skipEM = options?.skipEverMemOS ?? false

    const steps: LaunchStep[] = [
      { id: 'wait-docker', label: 'Wait for Docker', status: 'pending' },
      { id: 'compose-up', label: 'Start containers', status: 'pending' },
      { id: 'wait-mysql', label: 'Wait for MySQL', status: 'pending' },
      { id: 'init-tables', label: 'Initialize database', status: 'pending' },
      { id: 'wait-evermemos', label: 'Wait for EverMemOS infra', status: 'pending' },
      { id: 'start-services', label: 'Start services', status: 'pending' }
    ]

    const updateStep = (id: LaunchStepId, partial: Partial<LaunchStep>) => {
      const step = steps.find((s) => s.id === id)
      if (step) {
        Object.assign(step, partial)
        this.emit('launch-step', { ...step })
      }
    }

    try {
      // ─── Step 1: Wait for Docker daemon ─────────────────
      updateStep('wait-docker', { status: 'running', message: 'Checking Docker daemon...' })
      const dockerState = await detectDockerState()

      if (dockerState === 'not_installed') {
        updateStep('wait-docker', { status: 'error', message: 'Docker is not installed.' })
        return { success: false, error: 'Docker is not installed' }
      }

      if (dockerState === 'starting') {
        updateStep('wait-docker', { status: 'running', message: 'Docker daemon is starting, waiting...' })
        const ready = await waitForDockerReady(120000, 2000)
        if (!ready) {
          updateStep('wait-docker', { status: 'error', message: 'Docker daemon failed to become ready after 120s' })
          return { success: false, error: 'Docker daemon timeout' }
        }
      } else if (dockerState === 'not_running') {
        // Try to start it
        updateStep('wait-docker', { status: 'running', message: 'Starting Docker daemon...' })
        const started = await ensureDockerDaemon()
        if (!started) {
          updateStep('wait-docker', { status: 'error', message: 'Failed to start Docker daemon. Please start Docker Desktop manually.' })
          return { success: false, error: 'Failed to start Docker' }
        }
      }
      updateStep('wait-docker', { status: 'done', message: 'Docker is ready' })

      // ─── Step 2: docker compose up (with 500 retry) ─────
      const COMPOSE_TIMEOUT = 600000 // 10 minutes for image pull
      updateStep('compose-up', { status: 'running', message: 'Starting MySQL container...' })

      // Ensure Docker is truly healthy before compose
      const preComposeState = await detectDockerState()
      if (preComposeState !== 'healthy') {
        updateStep('compose-up', { status: 'running', message: 'Waiting for Docker to be fully ready...' })
        await waitForDockerReady(30000, 1000)
      }

      // Log which docker binary will be resolved
      const env = getExecEnv()
      console.log(`[launcher] compose-up: PATH starts with: ${env.PATH?.split(':').slice(0, 5).join(':')}`)

      let composeSuccess = false
      for (let attempt = 0; attempt < 3; attempt++) {
        try {
          // Try V2 → V1 → privileged
          let ok = false
          try {
            console.log('[launcher] compose-up: trying docker compose (V2)...')
            await spawnWithOutput('docker', ['compose', 'up', '-d'], {
              timeout: COMPOSE_TIMEOUT,
              onOutput: (line) => updateStep('compose-up', { status: 'running', message: line })
            })
            ok = true
            console.log('[launcher] compose-up: docker compose (V2) succeeded')
          } catch (e) {
            console.log(`[launcher] compose-up: V2 failed: ${e instanceof Error ? e.message : e}`)
          }

          if (!ok) {
            try {
              console.log('[launcher] compose-up: trying docker-compose (V1)...')
              await spawnWithOutput('docker-compose', ['up', '-d'], {
                timeout: COMPOSE_TIMEOUT,
                onOutput: (line) => updateStep('compose-up', { status: 'running', message: line })
              })
              ok = true
              console.log('[launcher] compose-up: docker-compose (V1) succeeded')
            } catch (e) {
              console.log(`[launcher] compose-up: V1 failed: ${e instanceof Error ? e.message : e}`)
            }
          }

          if (!ok) {
            console.log('[launcher] compose-up: trying with elevated privileges...')
            updateStep('compose-up', { status: 'running', message: 'Retrying with elevated privileges...' })
            await execWithPrivileges(
              `cd "${PROJECT_ROOT}" && (docker compose up -d || docker-compose up -d)`,
              { timeout: COMPOSE_TIMEOUT }
            )
            console.log('[launcher] compose-up: privileged compose succeeded')
          }

          composeSuccess = true
          break
        } catch (err) {
          const errMsg = err instanceof Error ? err.message : String(err)
          // Check if it's a 500 error (daemon still initializing)
          if (errMsg.includes('500') && attempt < 2) {
            updateStep('compose-up', {
              status: 'running',
              message: `Docker daemon not fully ready (500 error), retrying in 5s... (attempt ${attempt + 2}/3)`
            })
            await delay(5000)
            continue
          }
          throw err
        }
      }

      if (!composeSuccess) {
        updateStep('compose-up', { status: 'error', message: 'Failed to start containers' })
        return { success: false, error: 'docker compose up failed' }
      }

      // Start EverMemOS infrastructure
      if (!skipEM && isEverMemOSAvailable()) {
        updateStep('compose-up', { status: 'running', message: 'Starting EverMemOS infrastructure...' })
        const emResult = await startEverMemOS()
        if (!emResult.success) {
          // Non-blocking, just log
          console.error('[service-launcher] EverMemOS containers failed:', emResult.output)
        }
      }
      updateStep('compose-up', { status: 'done', message: 'Containers started' })

      // ─── Step 3: Wait for MySQL ─────────────────────────
      updateStep('wait-mysql', { status: 'running', message: 'Waiting for MySQL port...' })
      let mysqlReady = false
      for (let i = 0; i < 60; i++) {
        if (await isPortReachable(3306)) {
          mysqlReady = true
          break
        }
        if (i % 5 === 4) {
          updateStep('wait-mysql', {
            status: 'running',
            message: `Waiting for MySQL port... (${i + 1}s)`
          })
        }
        await delay(1000)
      }
      if (!mysqlReady) {
        updateStep('wait-mysql', { status: 'error', message: 'MySQL port timeout (60s)' })
        return { success: false, error: 'MySQL port timeout' }
      }
      // Extra buffer for MySQL initialization
      await delay(5000)
      updateStep('wait-mysql', { status: 'done', message: 'MySQL is ready' })

      // ─── Step 4: Initialize database tables ─────────────
      updateStep('init-tables', { status: 'running', message: 'Creating database tables...' })
      const createScript = join(TABLE_MGMT_DIR, 'create_all_tables.py')
      let tableCreated = false
      let lastTableErr = ''
      for (let attempt = 1; attempt <= 5; attempt++) {
        try {
          await execInProject('uv', ['run', 'python', createScript], { timeout: 60000 })
          tableCreated = true
          break
        } catch (err) {
          lastTableErr = err instanceof Error ? err.message : String(err)
          updateStep('init-tables', {
            status: 'running',
            message: `Attempt ${attempt} failed, ${attempt < 5 ? 'retrying...' : 'max retries reached'}`
          })
          if (attempt < 5) await delay(5000)
        }
      }
      if (!tableCreated) {
        updateStep('init-tables', { status: 'error', message: `Table creation failed: ${lastTableErr}` })
        return { success: false, error: 'Table creation failed' }
      }

      // Sync table schema (non-blocking)
      updateStep('init-tables', { status: 'running', message: 'Syncing table schema...' })
      try {
        const syncScript = join(TABLE_MGMT_DIR, 'sync_all_tables.py')
        await execInProject('uv', ['run', 'python', syncScript], { timeout: 60000 })
      } catch {
        // Schema sync failure doesn't block startup
      }
      updateStep('init-tables', { status: 'done', message: 'Database initialized' })

      // ─── Step 5: Wait for EverMemOS infrastructure ──────
      if (!skipEM && existsSync(EVERMEMOS_DIR)) {
        updateStep('wait-evermemos', { status: 'running', message: 'Waiting for EverMemOS infrastructure...' })
        let allInfraReady = true
        for (const { port, name } of EM_INFRA_PORTS) {
          let ready = false
          for (let i = 0; i < 180; i++) {
            if (await isPortReachable(port)) { ready = true; break }
            if (i % 5 === 4) {
              updateStep('wait-evermemos', {
                status: 'running',
                message: `Waiting for ${name}:${port}... (${i + 1}s)`
              })
            }
            await delay(1000)
          }
          if (!ready) {
            updateStep('wait-evermemos', {
              status: 'error',
              message: `${name}:${port} timeout after 180s — EverMemOS will not start`
            })
            allInfraReady = false
            skipEM = true
            break
          }
        }
        if (allInfraReady) {
          updateStep('wait-evermemos', { status: 'done', message: 'EverMemOS infrastructure ready' })
        }
      } else {
        updateStep('wait-evermemos', { status: 'skipped', message: 'EverMemOS not configured' })
      }

      // ─── Step 6: Start backend services ─────────────────
      updateStep('start-services', { status: 'running', message: 'Starting all services...' })
      await this.processManager.startAll({ skipEverMemOS: skipEM })
      updateStep('start-services', { status: 'done', message: 'All services started' })

      return { success: true }
    } catch (err) {
      const message = err instanceof Error ? err.message : String(err)
      return { success: false, error: message }
    }
  }
}
