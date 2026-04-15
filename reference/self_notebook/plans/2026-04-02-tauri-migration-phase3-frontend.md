# Phase 3: Frontend Unification — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Merge the Desktop Dashboard UI into the main `frontend/` React app as a new route, add mode selection, settings page, and platform abstraction layer. One unified React app for local, cloud-app, and cloud-web modes.

**Architecture:** Dashboard components migrate from `desktop/src/renderer/` to `frontend/src/`. New `PlatformBridge` abstraction replaces Electron IPC. Mode detection drives feature gating. All changes are additive — existing frontend routes and components stay untouched.

**Tech Stack:** React 19, TypeScript, React Router 7, Zustand, Tailwind CSS 4, Radix UI

---

## File Structure

### New Files

| File | Responsibility |
|------|---------------|
| `frontend/src/lib/platform.ts` | `PlatformBridge` interface + `TauriBridge` + `WebBridge` implementations |
| `frontend/src/stores/runtimeStore.ts` | Runtime config store: AppMode, UserType, feature flags |
| `frontend/src/pages/ModeSelectPage.tsx` | First-launch: choose Local or Cloud mode |
| `frontend/src/pages/SetupPage.tsx` | Local mode setup wizard (migrated from Desktop) |
| `frontend/src/pages/SystemPage.tsx` | Service management dashboard (migrated from Desktop Dashboard) |
| `frontend/src/pages/SettingsPage.tsx` | Model provider config + execution mode |
| `frontend/src/components/system/ServiceCard.tsx` | Service status card (migrated from Desktop) |
| `frontend/src/components/system/LogViewer.tsx` | Log viewer (migrated from Desktop) |
| `frontend/src/components/system/HealthStatusBar.tsx` | Overall health indicator |
| `frontend/src/types/platform.ts` | TypeScript types for platform bridge, process info, health, logs |

### Modified Files

| File | Change |
|------|--------|
| `frontend/src/App.tsx` | Add new routes: /mode-select, /setup, /app/system, /app/settings |
| `frontend/src/components/layout/Sidebar.tsx` | Add System and Settings nav items (mode-gated) |
| `frontend/src/components/layout/MainLayout.tsx` | Add mode-aware layout wrapper |
| `frontend/src/stores/configStore.ts` | Add `appMode` and `userType` fields |

---

## Task 1: Platform Types and Bridge Interface

**Files:**
- Create: `frontend/src/types/platform.ts`
- Create: `frontend/src/lib/platform.ts`

Define TypeScript types matching the Desktop's IPC types, then create the PlatformBridge abstraction:

```typescript
// types/platform.ts
export type AppMode = 'local' | 'cloud-app' | 'cloud-web'
export type UserType = 'internal' | 'external'

export interface ProcessInfo {
  serviceId: string
  label: string
  status: 'stopped' | 'starting' | 'running' | 'crashed'
  pid: number | null
  restartCount: number
  lastError: string | null
}

export type HealthState = 'unknown' | 'healthy' | 'unhealthy'

export interface ServiceHealth {
  serviceId: string
  label: string
  state: HealthState
  port: number | null
}

export interface OverallHealth {
  services: ServiceHealth[]
  allHealthy: boolean
}

export interface LogEntry {
  serviceId: string
  timestamp: number
  stream: 'stdout' | 'stderr'
  message: string
}

export interface AppConfig {
  mode: AppMode
  userType: UserType
  apiBaseUrl: string
}

export interface FeatureFlags {
  canUseClaudeCode: boolean
  canUseApiMode: boolean
  showSystemPage: boolean
  showSetupWizard: boolean
}
```

```typescript
// lib/platform.ts
export interface PlatformBridge {
  // Service management (local mode only)
  getServiceStatus(): Promise<ProcessInfo[]>
  startAllServices(): Promise<void>
  stopAllServices(): Promise<void>
  restartService(id: string): Promise<void>
  getLogs(serviceId?: string): Promise<LogEntry[]>
  onHealthUpdate(cb: (health: OverallHealth) => void): () => void
  onLog(cb: (entry: LogEntry) => void): () => void

  // App lifecycle
  getAppMode(): Promise<AppMode>
  getAppConfig(): Promise<AppConfig>
  isLocalMode(): boolean

  // External
  openExternal(url: string): Promise<void>
}

class TauriBridge implements PlatformBridge {
  // Delegates to @tauri-apps/api invoke() and listen()
  // Actual Tauri calls will be implemented in Phase 4
  // For now, methods throw 'Tauri not available'
}

class WebBridge implements PlatformBridge {
  // Cloud web mode: service management throws UnsupportedError
  // App config comes from server API
}

function detectPlatform(): PlatformBridge {
  if (typeof window !== 'undefined' && (window as any).__TAURI__) {
    return new TauriBridge()
  }
  return new WebBridge()
}

export const platform = detectPlatform()
```

---

## Task 2: Runtime Config Store

**Files:**
- Create: `frontend/src/stores/runtimeStore.ts`
- Modify: `frontend/src/stores/index.ts`

Zustand store for runtime mode, user type, and feature flags:

```typescript
interface RuntimeState {
  mode: AppMode
  userType: UserType
  features: FeatureFlags
  initialized: boolean

  setMode(mode: AppMode): void
  setUserType(type: UserType): void
  initialize(): Promise<void>
}
```

Feature flags derived from mode + userType:
- Local mode: showSystemPage=true, showSetupWizard=true (first launch), canUseClaudeCode=true
- Cloud + internal: canUseClaudeCode=true, showSystemPage=false
- Cloud + external: canUseClaudeCode=false, showSystemPage=false

---

## Task 3: System Page (Dashboard Migration)

**Files:**
- Create: `frontend/src/pages/SystemPage.tsx`
- Create: `frontend/src/components/system/ServiceCard.tsx`
- Create: `frontend/src/components/system/LogViewer.tsx`
- Create: `frontend/src/components/system/HealthStatusBar.tsx`

Migrate Dashboard from `desktop/src/renderer/pages/Dashboard.tsx` into the main frontend.

Key changes from Desktop version:
- Replace `window.nexus.*` IPC calls with `platform.*` calls from PlatformBridge
- Replace raw useState with React Query for service status polling
- Use existing Tailwind 4 / Radix UI patterns from the main frontend
- ServiceCard: adapt Desktop's ServiceCard component, use existing ui/Card base
- LogViewer: adapt Desktop's LogViewer, max 500 entries, service-color-coded, auto-scroll
- HealthStatusBar: simple bar showing overall health state at top of page

---

## Task 4: Settings Page

**Files:**
- Create: `frontend/src/pages/SettingsPage.tsx`

Model provider configuration page. Reference `desktop/src/renderer/components/setup/ProviderConfigView.tsx` (33KB) but create a simpler version:

- Provider selection dropdown (Anthropic, OpenAI, Google, Custom)
- Base URL input
- API Key input (masked)
- Model selection dropdown
- "Test Connection" button
- Execution mode selector (Claude Code / API) — gated by `features.canUseClaudeCode`
- Save button → persists to backend via API

This is a new page, not a migration. Keep it simple for V1.

---

## Task 5: Mode Selection Page

**Files:**
- Create: `frontend/src/pages/ModeSelectPage.tsx`

First-launch page shown when no mode is configured:

Two cards side by side:
- "Local Mode" — icon, description: "Everything runs on your machine. Offline capable."
- "Cloud Mode" — icon, description: "Connect to cloud services. Multi-device sync."

Selection persists to runtimeStore and localStorage. After selection:
- Local → redirect to /setup (first time) or /app/chat
- Cloud → redirect to /login

---

## Task 6: Route Integration

**Files:**
- Modify: `frontend/src/App.tsx`
- Modify: `frontend/src/components/layout/Sidebar.tsx`

Add new routes to App.tsx:
```
/mode-select    → ModeSelectPage (lazy)
/setup          → SetupPage (lazy, local mode only)
/app/system     → SystemPage (lazy, local mode only)
/app/settings   → SettingsPage (lazy)
```

Update Sidebar to show:
- System nav item (only when `features.showSystemPage`)
- Settings nav item (always visible)

Root route `/` logic:
- If not initialized → /mode-select
- If local mode, first launch → /setup
- If cloud mode, not logged in → /login
- Otherwise → /app/chat

---

## Task 7: Setup Page (Simplified)

**Files:**
- Create: `frontend/src/pages/SetupPage.tsx`

Simplified version of Desktop's SetupWizard for local mode first-launch:

Steps:
1. Initialize database (auto, show progress)
2. Start services (auto, show progress via PlatformBridge)
3. Configure model provider (inline SettingsPage content)
4. Done → redirect to /app/chat

Keep it minimal for V1. The full Setup Wizard from Desktop (preflight checks, dependency installation) is not needed because Tauri bundles everything.
