/**
 * providerApi — shared fetch/mutation helpers for the LLM provider wallet,
 * used by the Settings editor (ProviderSettings + the extracted
 * CustomEndpointForm/CliSignInPanel) and the Create Agent wizard's
 * provider step. Extracted from ProviderSettings.tsx so every surface
 * hits the same endpoints through the same identity-header logic instead
 * of drifting apart.
 */

import { getApiBaseUrl } from '@/stores/runtimeStore'

export interface ProviderCliStatus {
  cli_installed: boolean
  logged_in: boolean
  email: string | null
  expires_at: string | null
  allowed?: boolean
}

/** fetch wrapper that injects the identity headers configStore tracks.
 *
 * Two headers, mutually compatible:
 *   - Authorization: Bearer <jwt>  — cloud mode signed identity
 *   - X-User-Id: <user_id>         — local mode unsigned identity
 *
 * Sending both is intentional — the backend picks the right one for the
 * active mode and ignores the other. */
export function authFetch(input: RequestInfo | URL, init?: RequestInit): Promise<Response> {
  const headers = new Headers(init?.headers)
  try {
    const raw = localStorage.getItem('narra-nexus-config')
    if (raw) {
      const state = JSON.parse(raw)?.state || {}
      if (state.token) headers.set('Authorization', `Bearer ${state.token}`)
      if (state.userId) headers.set('X-User-Id', state.userId)
    }
  } catch {
    // Corrupt/absent localStorage config — proceed without auth headers;
    // the backend 401s if the request actually needed them.
  }
  return fetch(input, { ...init, headers })
}

export function providerUrl(path: string = ''): string {
  return `${getApiBaseUrl()}/api/providers${path}`
}

/** POST /api/providers. Callers own the post-success refresh (calling
 * their own onComplete/refreshConfig) — this function has no side
 * effects beyond the network call. */
export async function addProvider(
  body: Record<string, unknown>,
): Promise<{ ok: boolean; detail?: string }> {
  try {
    const res = await authFetch(providerUrl(), {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    }).then((r) => r.json())
    if (!res.success) return { ok: false, detail: res.detail }
    return { ok: true }
  } catch {
    return { ok: false }
  }
}

/** Stateless "verify before save" probe — nothing is persisted. */
export async function testProviderConfig(
  body: Record<string, unknown>,
): Promise<{ ok: boolean; msg?: string }> {
  try {
    const res = await authFetch(providerUrl('/test-config'), {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    }).then((r) => r.json()).catch(() => ({}))
    const msg = res.message || (typeof res.detail === 'string' ? res.detail : undefined)
    return { ok: !!res.success, msg }
  } catch {
    return { ok: false }
  }
}

export async function fetchClaudeStatus(): Promise<ProviderCliStatus | null> {
  try {
    const res = await authFetch(providerUrl('/claude-status')).then((r) => r.json())
    return res?.success ? res.data : null
  } catch {
    return null
  }
}

export async function fetchCodexStatus(): Promise<ProviderCliStatus | null> {
  try {
    const res = await authFetch(providerUrl('/codex-status')).then((r) => r.json())
    return res?.success ? res.data : null
  } catch {
    return null
  }
}
