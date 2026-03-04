/**
 * @file installer-registry.ts
 * @description Phase 2: Independent installers for each dependency
 *
 * Each installer has check() / install() methods and can be individually
 * retried or skipped. Logic extracted from process-manager.ts runAutoSetup.
 */

import { spawn, execFile } from 'child_process'
import { join } from 'path'
import { existsSync } from 'fs'
import { promisify } from 'util'
import { EventEmitter } from 'events'
import {
  PROJECT_ROOT,
  FRONTEND_DIR,
  EVERMEMOS_DIR,
  EVERMEMOS_GIT_URL
} from './constants'
import { getShellEnv } from './shell-env'
import { readEnv } from './env-manager'
import { resetComposeDetection } from './docker-manager'
import * as everMemOSEnv from './evermemos-env-manager'
import type { InstallerState, InstallerStatus } from '../shared/setup-types'

const execFileAsync = promisify(execFile)

// ─── Types ───────────────────────────────────────

export interface Installer {
  id: string
  label: string
  /** Dependencies: must be installed before this one */
  dependsOn?: string[]
  /** Whether failure blocks the whole flow */
  blocking: boolean
  /** Check if already installed/ready */
  check(): Promise<boolean>
  /** Run the installation */
  install(onOutput: (line: string) => void): Promise<void>
  /** Manual install URL for fallback */
  manualUrl?: string
}

// ─── Execution Environment ───────────────────────────

function getExecEnv(): Record<string, string> {
  const shellEnv = getShellEnv()
  const dotEnv = readEnv()
  const nonEmptyDotEnv: Record<string, string> = {}
  for (const [key, value] of Object.entries(dotEnv)) {
    if (value.trim()) nonEmptyDotEnv[key] = value
  }
  const noProxyHosts = 'localhost,127.0.0.1'
  return { ...shellEnv, ...nonEmptyDotEnv, NO_PROXY: noProxyHosts, no_proxy: noProxyHosts }
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

/**
 * Execute sudo-required commands via system native privilege elevation dialog
 * (macOS: osascript, Linux: pkexec)
 */
async function execWithPrivileges(
  script: string,
  options?: { timeout?: number }
): Promise<{ stdout: string; stderr: string }> {
  if (process.platform === 'darwin') {
    const extraPaths = [
      '/usr/local/bin',
      '/opt/homebrew/bin',
      '/Applications/Docker.app/Contents/Resources/bin',
    ].join(':')
    const fullScript = `export PATH="${extraPaths}:$PATH" && ${script}`
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
        console.log(`[installer] ${line}`)
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

    proc.on('error', (err) => {
      clearTimeout(timer)
      reject(err)
    })
  })
}

// ─── Installer Definitions ───────────────────────────

function createUvInstaller(): Installer {
  return {
    id: 'uv',
    label: 'uv (Python package manager)',
    blocking: true,
    manualUrl: 'https://docs.astral.sh/uv/getting-started/installation/',
    async check() {
      try {
        await execInProject('uv', ['--version'], { timeout: 10000 })
        return true
      } catch { return false }
    },
    async install(onOutput) {
      onOutput('Installing uv...')
      await spawnWithOutput('sh', ['-c', 'curl -LsSf https://astral.sh/uv/install.sh | sh'], {
        timeout: 300000, onOutput
      })
    }
  }
}

function createClaudeInstaller(): Installer {
  return {
    id: 'claude',
    label: 'Claude Code CLI',
    blocking: false,
    async check() {
      try {
        await execInProject('claude', ['--version'], { timeout: 10000 })
        return true
      } catch { return false }
    },
    async install(onOutput) {
      onOutput('Installing Claude Code...')
      await spawnWithOutput('sh', ['-c', 'curl -fsSL https://claude.ai/install.sh | sh'], {
        timeout: 300000, onOutput
      })
    }
  }
}

function createPythonDepsInstaller(): Installer {
  return {
    id: 'python-deps',
    label: 'Python dependencies',
    dependsOn: ['uv'],
    blocking: true,
    async check() {
      return existsSync(join(PROJECT_ROOT, '.venv'))
    },
    async install(onOutput) {
      onOutput('Running uv sync...')
      await spawnWithOutput('uv', ['sync'], { timeout: 600000, onOutput })
    }
  }
}

function createDockerInstaller(): Installer {
  return {
    id: 'docker',
    label: 'Docker',
    blocking: true,
    manualUrl: 'https://www.docker.com/products/docker-desktop/',
    async check() {
      try {
        await execInProject('docker', ['info'], { timeout: 10000 })
        return true
      } catch { return false }
    },
    async install(onOutput) {
      if (process.platform === 'darwin') {
        await installDockerMacOS(onOutput)
      } else {
        await installDockerLinux(onOutput)
      }
    }
  }
}

/**
 * Start Colima: try as regular user first, then retry with admin privileges.
 * Colima needs sudo on macOS for VM networking setup.
 */
async function startColima(
  colimaCmd: string,
  onOutput: (line: string) => void,
  tag: string
): Promise<void> {
  try {
    await execInProject(colimaCmd, ['start'], { timeout: 300000 })
    return
  } catch {
    onOutput(`${tag} Colima needs admin privileges, retrying...`)
  }
  await execWithPrivileges(`${colimaCmd} start`, { timeout: 300000 })
}

async function installDockerMacOS(onOutput: (line: string) => void): Promise<void> {
  // Strategy 1: Launch Docker Desktop (only if Docker.app exists)
  if (existsSync('/Applications/Docker.app')) {
    onOutput('[Strategy 1/4] Launching Docker Desktop...')
    try {
      await execInProject('open', ['-a', 'Docker'], { timeout: 10000 })
      for (let i = 0; i < 30; i++) {
        await new Promise((r) => setTimeout(r, 2000))
        try {
          await execInProject('docker', ['info'], { timeout: 5000 })
          onOutput('Docker Desktop is ready')
          return
        } catch { /* not ready yet */ }
        if (i % 5 === 4) onOutput(`Waiting for Docker Desktop to start... (${(i + 1) * 2}s)`)
      }
      onOutput('Docker Desktop launch timed out, trying next strategy...')
    } catch {
      onOutput('Docker Desktop failed to launch, trying next strategy...')
    }
  } else {
    onOutput('[Strategy 1/4] Docker Desktop not found at /Applications/Docker.app, skipping')
  }

  // Strategy 2: Start Colima (only if installed)
  try {
    await execInProject('colima', ['version'], { timeout: 5000 })
    onOutput('[Strategy 2/4] Starting Colima...')
    await execInProject('colima', ['start'], { timeout: 300000 })
    await execInProject('docker', ['info'], { timeout: 10000 })
    onOutput('Colima started successfully')
    return
  } catch {
    onOutput('[Strategy 2/4] Colima not available, trying next strategy...')
  }

  // Strategy 3: brew install colima + docker
  try {
    await execInProject('brew', ['--version'], { timeout: 10000 })
    onOutput('[Strategy 3/4] Installing Docker via Homebrew (colima + docker CLI)...')
    await spawnWithOutput('brew', ['install', 'colima', 'docker', 'docker-compose'], {
      timeout: 600000, onOutput
    })
    onOutput('[Strategy 3/4] Starting Colima VM...')
    await startColima('colima', onOutput, '[Strategy 3/4]')
    await execInProject('docker', ['info'], { timeout: 10000 })
    onOutput('Docker installed via Homebrew + Colima')
    resetComposeDetection()
    return
  } catch {
    onOutput('[Strategy 3/4] Homebrew install failed, trying next strategy...')
  }

  // Strategy 4: Install Homebrew (as regular user) + colima + docker
  // Homebrew refuses to run as root, so we CANNOT use execWithPrivileges for it.
  // Instead: prepare /opt/homebrew with privileges, then install Homebrew as regular user.
  onOutput('[Strategy 4/4] Installing Homebrew + Docker (admin privileges for directory setup only)...')
  try {
    const brewPrefix = process.arch === 'arm64' ? '/opt/homebrew' : '/usr/local'
    const brewPath = `${brewPrefix}/bin/brew`
    const currentUser = process.env.USER || process.env.LOGNAME || 'nobody'

    // Step 1: Create Homebrew prefix directory with correct ownership (needs root)
    if (!existsSync(brewPath)) {
      onOutput('[Strategy 4/4] Preparing Homebrew directory (admin privileges required)...')
      await execWithPrivileges(
        `mkdir -p "${brewPrefix}" && chown -R ${currentUser}:admin "${brewPrefix}"`,
        { timeout: 30000 }
      )

      // Step 2: Install Homebrew as regular user (NO root — Homebrew requires this)
      onOutput('[Strategy 4/4] Installing Homebrew (as regular user)...')
      await spawnWithOutput('bash', ['-c',
        'NONINTERACTIVE=1 /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"'
      ], { timeout: 600000, onOutput })
    } else {
      onOutput('[Strategy 4/4] Homebrew already installed, skipping Homebrew installation')
    }

    // Step 3: Install colima + docker CLI
    onOutput('[Strategy 4/4] Installing colima + docker CLI via Homebrew...')
    await spawnWithOutput(brewPath, ['install', 'colima', 'docker', 'docker-compose'], {
      timeout: 600000, onOutput
    })

    // Step 4: Start Colima
    const colimaPath = `${brewPrefix}/bin/colima`
    onOutput('[Strategy 4/4] Starting Colima VM...')
    await startColima(colimaPath, onOutput, '[Strategy 4/4]')
    await execInProject('docker', ['info'], { timeout: 10000 })
    onOutput('Docker installed via Homebrew + Colima')
    resetComposeDetection()
    return
  } catch {
    onOutput('[Strategy 4/4] Homebrew + Docker install failed')
  }
}

async function installDockerLinux(onOutput: (line: string) => void): Promise<void> {
  // Strategy 1: systemctl start (unprivileged)
  onOutput('[Strategy 1/3] Trying to start Docker daemon...')
  try {
    await execInProject('systemctl', ['start', 'docker'], { timeout: 30000 })
    await execInProject('docker', ['info'], { timeout: 10000 })
    onOutput('Docker daemon started')
    return
  } catch {
    onOutput('[Strategy 1/3] Cannot start Docker without privileges, trying next strategy...')
  }

  // Strategy 2: Privileged systemctl
  onOutput('[Strategy 2/3] Starting Docker daemon (admin privileges required)...')
  try {
    await execWithPrivileges('systemctl start docker', { timeout: 30000 })
    await execInProject('docker', ['info'], { timeout: 10000 })
    onOutput('Docker daemon started with admin privileges')
    return
  } catch {
    onOutput('[Strategy 2/3] Docker not installed or start failed, trying next strategy...')
  }

  // Strategy 3: Install via get.docker.com
  onOutput('[Strategy 3/3] Installing Docker Engine via get.docker.com (admin privileges required)...')
  try {
    const user = process.env.USER || 'root'
    await execWithPrivileges(
      'curl -fsSL https://get.docker.com | sh'
      + ' && (apt-get install -y docker-compose-plugin 2>/dev/null'
      + '    || yum install -y docker-compose-plugin 2>/dev/null'
      + '    || true)'
      + ` && usermod -aG docker ${user}`
      + ' && systemctl start docker',
      { timeout: 600000 }
    )
    resetComposeDetection()
    try {
      await execInProject('docker', ['info'], { timeout: 10000 })
    } catch {
      await execWithPrivileges('docker info', { timeout: 10000 })
    }
    onOutput('Docker Engine installed and started')
    return
  } catch {
    onOutput('[Strategy 3/3] Docker installation failed or cancelled by user')
  }

  throw new Error('Docker installation failed. Please install Docker manually.')
}

function createEverMemosCloneInstaller(): Installer {
  return {
    id: 'evermemos-clone',
    label: 'Clone EverMemOS',
    blocking: false,
    async check() {
      return everMemOSEnv.isCloned()
    },
    async install(onOutput) {
      if (everMemOSEnv.isCloned()) {
        everMemOSEnv.flushPendingEnv()
        return
      }
      onOutput('Cloning EverMemOS repository...')
      await spawnWithOutput('git', ['clone', '--depth', '1', '--progress', EVERMEMOS_GIT_URL, '.evermemos'], {
        timeout: 600000, onOutput
      })
      everMemOSEnv.flushPendingEnv()
    }
  }
}

function createEverMemosDepsInstaller(): Installer {
  return {
    id: 'evermemos-deps',
    label: 'EverMemOS dependencies',
    dependsOn: ['uv', 'evermemos-clone'],
    blocking: false,
    async check() {
      return existsSync(join(EVERMEMOS_DIR, '.venv'))
    },
    async install(onOutput) {
      if (!existsSync(EVERMEMOS_DIR)) {
        throw new Error('EverMemOS directory not found, skipping')
      }
      onOutput('Installing EverMemOS Python dependencies...')
      await spawnWithOutput('uv', ['sync'], {
        cwd: EVERMEMOS_DIR, timeout: 600000, onOutput
      })
    }
  }
}

function createFrontendBuildInstaller(): Installer {
  return {
    id: 'frontend-build',
    label: 'Build frontend',
    blocking: true,
    async check() {
      return existsSync(join(FRONTEND_DIR, 'dist', 'index.html'))
    },
    async install(onOutput) {
      onOutput('Installing npm packages...')
      await spawnWithOutput('npm', ['install', '--no-audit', '--no-fund'], {
        cwd: FRONTEND_DIR, timeout: 300000, onOutput
      })
      onOutput('Compiling frontend...')
      await spawnWithOutput('npm', ['run', 'build'], {
        cwd: FRONTEND_DIR, timeout: 300000, onOutput
      })
    }
  }
}

// ─── Installer Registry ───────────────────────────────

export class InstallerRegistry extends EventEmitter {
  private installers: Installer[]
  private states = new Map<string, InstallerState>()

  constructor() {
    super()
    this.installers = [
      createUvInstaller(),
      createClaudeInstaller(),
      createPythonDepsInstaller(),
      createDockerInstaller(),
      createEverMemosCloneInstaller(),
      createEverMemosDepsInstaller(),
      createFrontendBuildInstaller()
    ]
    for (const inst of this.installers) {
      this.states.set(inst.id, {
        id: inst.id,
        label: inst.label,
        status: 'pending',
        canSkip: !inst.blocking
      })
    }
  }

  /** Get current state of all installers */
  getAllStates(): InstallerState[] {
    return this.installers.map((inst) => this.states.get(inst.id)!)
  }

  /** Install a single dependency by ID */
  async install(id: string): Promise<void> {
    const inst = this.installers.find((i) => i.id === id)
    if (!inst) throw new Error(`Unknown installer: ${id}`)

    this.updateState(id, { status: 'running', currentOutput: '', error: undefined })
    try {
      await inst.install((line) => {
        this.updateState(id, { status: 'running', currentOutput: line })
      })
      this.updateState(id, { status: 'done', currentOutput: undefined })
    } catch (err) {
      const error = err instanceof Error ? err.message : String(err)
      this.updateState(id, { status: 'error', error, currentOutput: undefined })
      throw err
    }
  }

  /** Retry a failed installation */
  async retry(id: string): Promise<void> {
    return this.install(id)
  }

  /** Skip an installer (mark as skipped) */
  skip(id: string): void {
    this.updateState(id, { status: 'skipped' })
  }

  /**
   * Install all missing dependencies in order.
   * Respects dependency ordering (uv before python-deps, etc.)
   * @param missingIds IDs of missing dependencies to install
   */
  async installAll(missingIds: string[]): Promise<{ success: boolean; failedId?: string }> {
    // Filter to only known installers, preserving registry order
    const toInstall = this.installers.filter((inst) => missingIds.includes(inst.id))

    for (const inst of toInstall) {
      // Check if already done
      const current = this.states.get(inst.id)
      if (current?.status === 'done' || current?.status === 'skipped') continue

      // Check dependencies
      if (inst.dependsOn) {
        const unmet = inst.dependsOn.filter((depId) => {
          const depState = this.states.get(depId)
          return depState?.status !== 'done'
        })
        if (unmet.length > 0) {
          // Skip if dependencies not met and installer is non-blocking
          if (!inst.blocking) {
            this.skip(inst.id)
            continue
          }
          // For blocking ones, dependencies should have been installed before
        }
      }

      try {
        // First check if already ready (might have been installed externally)
        if (await inst.check()) {
          this.updateState(inst.id, { status: 'done' })
          continue
        }
        await this.install(inst.id)
      } catch {
        if (inst.blocking) {
          return { success: false, failedId: inst.id }
        }
        // Non-blocking: continue with others
      }
    }
    return { success: true }
  }

  private updateState(id: string, partial: Partial<InstallerState>): void {
    const current = this.states.get(id)
    if (!current) return
    const updated = { ...current, ...partial }
    this.states.set(id, updated)
    this.emit('installer-update', updated)
  }
}
