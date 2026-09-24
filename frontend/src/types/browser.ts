/**
 * @file_name: browser.ts
 * @date: 2026-09-22
 * @description: Shapes returned by /api/browser/* — the in-app browser runtime.
 *
 * `state` is the only thing callers should branch on; `reason` exists so the
 * UI can tell "never installed" from "installed but will not start", which
 * need different words and a different button.
 */

export type BrowserRuntimeState = 'absent' | 'installing' | 'ready';
export type BrowserRuntimeSource = 'managed' | 'system';
export type BrowserRuntimeMode = 'headless' | 'headed';

export interface BrowserPage {
  id: string;
  title: string;
  url: string;
  opener_id: string | null;
}

export interface BrowserInstallProgress {
  phase: string;
  bytes_done: number;
  /** Null when the server sent no Content-Length — render indeterminate. */
  bytes_total: number | null;
  /** Null for the same reason; never invent one from an unknown total. */
  percent: number | null;
}

export interface BrowserRuntimeStatus {
  state: BrowserRuntimeState;
  reason: string;
  version: string | null;
  executable: string | null;
  progress: BrowserInstallProgress | null;
  manual_install?: BrowserManualInstallHelp | null;
  selection?: {
    source: BrowserRuntimeSource;
    mode: BrowserRuntimeMode;
    editable: boolean;
    system_executable: string | null;
  };
}

/** Commands come from the running installer's interpreter, paths and mirror configuration. */
export interface BrowserManualInstallHelp {
  root: string;
  shell: 'posix' | 'powershell';
  argv: string[];
  command: string;
  status_command: string;
  cancel_command: string;
  manifest_url: string;
  download_host: string | null;
  mirror_flags: { manifest_url: string; download_host: string };
}

export interface BrowserInstallResult {
  ok: boolean;
  error: string | null;
  status: Omit<BrowserRuntimeStatus, 'progress'>;
  manual_install?: BrowserManualInstallHelp | null;
}

/** How long an approval lasts. `always` is written into the agent's policy. */
export type ApprovalLifetime = 'turn' | 'thread' | 'always';

export interface PendingApproval {
  id: string;
  agent_id: string;
  /** The exact origin, e.g. "https://www.baidu.com" — never "a website". */
  origin: string;
  /** Independent privileged operations; ordinary browsing is never prompted. */
  capability: 'downloads' | 'uploads' | 'auto_review';
  requested_at: number;
  /** Only scopes backed by a trusted tool-call context may be granted. */
  allowed_lifetimes?: ApprovalLifetime[];
}

/** Login requests share the pending-notice endpoint but never resolve as permissions. */
export interface BrowserLoginRequest {
  kind: 'login';
  id: string;
  agent_id: string;
  session_id: string;
  reason: string;
  state: 'pending' | 'in_control';
  requested_at: string;
}

export type BrowserPendingRequest = PendingApproval | BrowserLoginRequest;

export type BrowserPolicyVerdict = 'allow' | 'ask' | 'deny';
export type BrowserPolicyCapability = 'full_cdp_access';
export type BrowserPolicyRule = { origin: string; capability: BrowserPolicyCapability; verdict: 'allow' | 'deny' };

export interface BrowserPolicyView {
  agent_id: string;
  defaults: { full_cdp_access: BrowserPolicyVerdict };
  origins: Array<{
    origin: string;
    full_cdp_access: BrowserPolicyVerdict;
  }>;
}
