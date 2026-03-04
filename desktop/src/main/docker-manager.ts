/**
 * @file docker-manager.ts
 * @description Docker Compose container lifecycle management
 *
 * Manages Docker containers for MySQL (primary database) and EverMemOS (optional).
 * Invokes docker compose commands via child_process.
 */

import { execFile } from 'child_process'
import { promisify } from 'util'
import { existsSync } from 'fs'
import { DOCKER_COMPOSE_PATH, EVERMEMOS_COMPOSE_PATH, PORTS } from './constants'
import { getShellEnv } from './shell-env'

const execFileAsync = promisify(execFile)

// ─── Type Definitions ───────────────────────────────────────

export interface ContainerStatus {
  name: string
  state: 'running' | 'stopped' | 'not_found'
  ports: string
}

export interface DockerGroupStatus {
  id: string
  label: string
  containers: ContainerStatus[]
  composePath: string
  available: boolean
}

// ─── Utility Functions ───────────────────────────────────────

/** Safe command execution (uses login shell env + common extra paths to ensure docker/colima are found) */
async function execSafe(
  cmd: string,
  args: string[],
  options: { timeout?: number } = {}
): Promise<{ stdout: string; stderr: string; success: boolean }> {
  try {
    const env = getShellEnv()
    // Ensure common tool paths are included (Homebrew, Docker Desktop)
    // These may not be in the cached shell env if tools were installed during this session
    const extraPaths = ['/opt/homebrew/bin', '/usr/local/bin', '/Applications/Docker.app/Contents/Resources/bin']
    const currentPath = env.PATH || ''
    const missingPaths = extraPaths.filter(p => !currentPath.includes(p))
    if (missingPaths.length > 0) {
      env.PATH = [...missingPaths, currentPath].join(':')
    }
    const result = await execFileAsync(cmd, args, {
      timeout: options.timeout ?? 30000,
      env
    })
    return { ...result, success: true }
  } catch (err: unknown) {
    const message = err instanceof Error ? err.message : String(err)
    return { stdout: '', stderr: message, success: false }
  }
}

/**
 * Detect available compose command style (cached)
 *
 * - V2 plugin: docker compose (Docker Desktop or docker-compose-plugin package)
 * - V1 standalone: docker-compose (Homebrew or pip install)
 */
let composeStyle: 'v2' | 'v1' | null = null

async function detectComposeStyle(): Promise<'v2' | 'v1'> {
  if (composeStyle) return composeStyle

  const v2 = await execSafe('docker', ['compose', 'version'], { timeout: 5000 })
  if (v2.success) { composeStyle = 'v2'; return 'v2' }

  const v1 = await execSafe('docker-compose', ['version'], { timeout: 5000 })
  if (v1.success) { composeStyle = 'v1'; return 'v1' }

  // Default to V2 when neither is available, for clearer error messages
  composeStyle = 'v2'
  return 'v2'
}

/** Reset cache (need to re-detect after Docker installation) */
export function resetComposeDetection(): void {
  composeStyle = null
}

/** Get docker compose command prefix (auto-detect V1/V2) */
async function composeCmd(composePath: string): Promise<{ cmd: string; baseArgs: string[] }> {
  const style = await detectComposeStyle()
  return style === 'v1'
    ? { cmd: 'docker-compose', baseArgs: ['-f', composePath] }
    : { cmd: 'docker', baseArgs: ['compose', '-f', composePath] }
}

// ─── Docker Status Detection ─────────────────────────────────

export type DockerState = 'not_installed' | 'not_running' | 'starting' | 'healthy'

/**
 * Fine-grained Docker daemon state detection
 *
 * docker --version fail   → not_installed
 * docker info success     → healthy
 * docker info stderr 500  → starting (daemon initializing, need to wait)
 * docker info other error → not_running (daemon not started)
 */
export async function detectDockerState(): Promise<DockerState> {
  // Step 1: check if docker CLI is installed
  const versionResult = await execSafe('docker', ['--version'], { timeout: 5000 })
  if (!versionResult.success) return 'not_installed'

  // Step 2: check daemon status
  const infoResult = await execSafe('docker', ['info'], { timeout: 10000 })
  if (infoResult.success) return 'healthy'

  // Distinguish between "starting" (500 error) and "not running"
  const errMsg = infoResult.stderr.toLowerCase()
  if (errMsg.includes('500') || errMsg.includes('is the docker daemon running')) {
    // Could be either starting or not running — check if Docker Desktop/containerd process exists
    const isStarting = errMsg.includes('500')
    return isStarting ? 'starting' : 'not_running'
  }

  return 'not_running'
}

/**
 * Wait for Docker daemon to become healthy (poll detectDockerState)
 * @param maxWaitMs Maximum wait time in milliseconds (default 120s)
 * @param intervalMs Poll interval in milliseconds (default 2s)
 */
export async function waitForDockerReady(maxWaitMs = 120000, intervalMs = 2000): Promise<boolean> {
  const deadline = Date.now() + maxWaitMs
  while (Date.now() < deadline) {
    const state = await detectDockerState()
    if (state === 'healthy') return true
    if (state === 'not_installed') return false
    await new Promise((r) => setTimeout(r, intervalMs))
  }
  return false
}

/** Check if Docker daemon is ready */
export async function isDockerReady(): Promise<boolean> {
  const result = await execSafe('docker', ['info'], { timeout: 10000 })
  return result.success
}

/**
 * Ensure Docker daemon is available.
 * Called before Dashboard "Start All" to handle daemon not running after reboot.
 *
 * macOS: Docker Desktop → Colima
 * Linux: systemctl → pkexec systemctl
 */
export async function ensureDockerDaemon(): Promise<boolean> {
  if (await isDockerReady()) return true

  if (process.platform === 'darwin') {
    // Strategy 1: Launch Docker Desktop (only if Docker.app exists)
    if (existsSync('/Applications/Docker.app')) {
      try {
        await execSafe('open', ['-a', 'Docker'], { timeout: 10000 })
        for (let i = 0; i < 30; i++) {
          await new Promise((r) => setTimeout(r, 2000))
          if (await isDockerReady()) return true
        }
      } catch { /* Docker Desktop failed to launch */ }
    }

    // Strategy 2: Start Colima
    // Try colima in common paths (might not be in cached shell PATH if just installed)
    const colimaPaths = ['colima']
    if (process.arch === 'arm64') {
      colimaPaths.push('/opt/homebrew/bin/colima')
    } else {
      colimaPaths.push('/usr/local/bin/colima')
    }

    for (const colima of colimaPaths) {
      // Try as regular user first
      await execSafe(colima, ['start'], { timeout: 300000 })
      if (await isDockerReady()) return true

      // Colima may need sudo for VM networking — retry with privileges
      try {
        const { execFile: ef } = require('child_process')
        const { promisify: p } = require('util')
        const execAsync = p(ef)
        if (process.platform === 'darwin') {
          const script = `${colima} start`
          const escaped = script.replace(/\\/g, '\\\\').replace(/"/g, '\\"')
          await execAsync('osascript', ['-e',
            `do shell script "${escaped}" with administrator privileges`
          ], { timeout: 300000, env: getShellEnv() })
        }
      } catch { /* privileges denied or failed */ }
      if (await isDockerReady()) return true
    }
  } else {
    // Linux strategy 1: direct systemctl (may already be in docker group)
    await execSafe('systemctl', ['start', 'docker'], { timeout: 30000 })
    if (await isDockerReady()) return true

    // Linux strategy 2: privileged systemctl (pkexec password prompt)
    await execSafe('pkexec', ['systemctl', 'start', 'docker'], { timeout: 30000 })
    if (await isDockerReady()) return true
  }

  return false
}

// ─── MySQL (Primary Database) ──────────────────────────────

/** Start MySQL containers */
export async function startMySQL(): Promise<{ success: boolean; output: string }> {
  const { cmd, baseArgs } = await composeCmd(DOCKER_COMPOSE_PATH)
  // 首次拉取镜像可能耗时较长，给充足超时（10 分钟）
  const result = await execSafe(cmd, [...baseArgs, 'up', '-d'], { timeout: 600000 })
  return {
    success: result.success,
    output: result.success ? result.stdout : result.stderr
  }
}

/** Stop MySQL containers */
export async function stopMySQL(): Promise<{ success: boolean; output: string }> {
  const { cmd, baseArgs } = await composeCmd(DOCKER_COMPOSE_PATH)
  const result = await execSafe(cmd, [...baseArgs, 'down'], { timeout: 30000 })
  return {
    success: result.success,
    output: result.success ? result.stdout : result.stderr
  }
}

/** Get MySQL container status */
export async function getMySQLStatus(): Promise<ContainerStatus[]> {
  return getComposeStatus(DOCKER_COMPOSE_PATH)
}

/** Check if MySQL port is reachable */
export async function isMySQLReady(): Promise<boolean> {
  return checkPort(PORTS.MYSQL)
}

// ─── EverMemOS (Optional) ──────────────────────────────

/** Check if EverMemOS configuration exists */
export function isEverMemOSAvailable(): boolean {
  return existsSync(EVERMEMOS_COMPOSE_PATH)
}

/** Start EverMemOS containers */
export async function startEverMemOS(): Promise<{ success: boolean; output: string }> {
  if (!isEverMemOSAvailable()) {
    return { success: false, output: 'EverMemOS config file not found' }
  }
  const { cmd, baseArgs } = await composeCmd(EVERMEMOS_COMPOSE_PATH)
  // EverMemOS 含 MongoDB/ES/Milvus/Redis 四个镜像，首次拉取需要充足超时
  const result = await execSafe(cmd, [...baseArgs, 'up', '-d'], { timeout: 600000 })
  return {
    success: result.success,
    output: result.success ? result.stdout : result.stderr
  }
}

/** Stop EverMemOS containers */
export async function stopEverMemOS(): Promise<{ success: boolean; output: string }> {
  if (!isEverMemOSAvailable()) return { success: true, output: '' }
  const { cmd, baseArgs } = await composeCmd(EVERMEMOS_COMPOSE_PATH)
  const result = await execSafe(cmd, [...baseArgs, 'down'], { timeout: 30000 })
  return {
    success: result.success,
    output: result.success ? result.stdout : result.stderr
  }
}

// ─── Common Operations ───────────────────────────────────────

/** Start all Docker containers (MySQL + optional EverMemOS) */
export async function startAll(): Promise<{ mysql: boolean; evermemos: boolean }> {
  // Ensure Docker daemon is available (Colima may not be running after reboot)
  await ensureDockerDaemon()

  const mysqlResult = await startMySQL()
  let evermemosResult = { success: true }
  if (isEverMemOSAvailable()) {
    evermemosResult = await startEverMemOS()
  }
  return {
    mysql: mysqlResult.success,
    evermemos: evermemosResult.success
  }
}

/** Stop all Docker containers */
export async function stopAll(): Promise<void> {
  await Promise.all([stopMySQL(), stopEverMemOS()])
}

/** Get all Docker group statuses */
export async function getAllStatus(): Promise<DockerGroupStatus[]> {
  const groups: DockerGroupStatus[] = [
    {
      id: 'mysql',
      label: 'MySQL',
      containers: await getComposeStatus(DOCKER_COMPOSE_PATH),
      composePath: DOCKER_COMPOSE_PATH,
      available: existsSync(DOCKER_COMPOSE_PATH)
    }
  ]

  if (isEverMemOSAvailable()) {
    groups.push({
      id: 'evermemos',
      label: 'EverMemOS',
      containers: await getComposeStatus(EVERMEMOS_COMPOSE_PATH),
      composePath: EVERMEMOS_COMPOSE_PATH,
      available: true
    })
  }

  return groups
}

// ─── Internal Utilities ───────────────────────────────────────

/** Get container status for a given compose file */
async function getComposeStatus(composePath: string): Promise<ContainerStatus[]> {
  if (!existsSync(composePath)) return []

  const { cmd, baseArgs } = await composeCmd(composePath)
  const result = await execSafe(cmd, [
    ...baseArgs,
    'ps',
    '--format',
    'json'
  ])

  if (!result.success || !result.stdout.trim()) return []

  try {
    // docker compose ps --format json outputs one JSON object per line
    const lines = result.stdout.trim().split('\n')
    return lines.map((line) => {
      const data = JSON.parse(line)
      return {
        name: data.Name || data.Service || 'unknown',
        state: (data.State === 'running' ? 'running' : 'stopped') as 'running' | 'stopped',
        ports: data.Ports || ''
      }
    })
  } catch {
    return []
  }
}

/** Check if a port is reachable */
async function checkPort(port: number, host = '127.0.0.1'): Promise<boolean> {
  return new Promise((resolve) => {
    const net = require('net')
    const socket = new net.Socket()
    socket.setTimeout(2000)
    socket.on('connect', () => {
      socket.destroy()
      resolve(true)
    })
    socket.on('error', () => resolve(false))
    socket.on('timeout', () => {
      socket.destroy()
      resolve(false)
    })
    socket.connect(port, host)
  })
}
