/**
 * @file_name: providersApi.ts
 * @author: NarraNexus
 * @date: 2026-08-28
 * @description: Shared types + error mapping for /api/providers callers,
 * plus ProviderSettings' LEGACY raw-fetch plumbing.
 *
 * The transport for NEW code lives on ApiClient (api.addProvider /
 * api.getClaudeStatus / api.getCodexStatus / api.getProviders): it is the
 * only client with the session-death guard (401 + confirmSessionDeath —
 * the 2026-08-02 /api/providers 401 incident lives on exactly this
 * resource) and the FastAPI `detail` extraction. Do NOT route new
 * endpoints through authFetch below.
 *
 * authFetch/providerApiUrl remain ONLY for ProviderSettings' pre-existing
 * raw endpoints (delete / test / models / sync) — migrating those is
 * tracked as a follow-up, not silently in this module's mandate.
 */
import { ApiError, api } from '@/lib/api';
import { getAuthHeaders } from '@/lib/authHeaders';
import { getApiBaseUrl } from '@/stores/runtimeStore';

/** THE provider-row shape — the one type every frontend consumer uses
 * (GET /api/providers serializes exactly these; optionality mirrors the
 * backend contract that ProviderSettings has trusted all along). Extend
 * HERE, never with a local re-declaration: a third row shape is how the
 * as-unknown-as casts come back. */
export interface ProviderRow {
  provider_id: string;
  name: string;
  source: string;
  protocol: string;
  auth_type: string;
  is_active: boolean;
  models: string[];
  api_key_masked?: string;
  base_url?: string;
  /** NetMind account this key belongs to (captured at mint). */
  netmind_account_email?: string;
}

/** /claude-status and /codex-status payload. `allowed` is false ONLY
 * when the backend gated this caller out (cloud non-staff — the same
 * predicate that 403s OAuth card types); it is undefined on local and
 * for cloud staff, so consumers must check `=== false`. */
export interface CliStatusPayload {
  cli_installed: boolean;
  logged_in: boolean;
  email: string | null;
  expires_at: string | null;
  allowed?: boolean;
}

/** Map a failed api.addProvider call to user-facing copy — one mapping
 * for every caller, so /setup and Settings never word the same failure
 * differently. A non-empty backend detail is shown verbatim (it carries
 * the actual reason); an ApiError without detail (non-JSON error body,
 * e.g. a gateway 502 page) gets the generic failure copy; anything else
 * (fetch itself rejected) is a network-level failure. */
export function providerErrorMessage(
  err: unknown,
  t: (key: string) => string,
): string {
  if (err instanceof ApiError) {
    return err.detail || t('settings.provider.failed');
  }
  return t('settings.provider.networkError');
}

/** POST a provider card and map every failure to user copy — the ONE
 * wrapper both /setup and Settings use (they used to carry verbatim
 * copies). NO refresh side effects: each caller owns its own refresh
 * choreography (a deliberate decision — see the SetupPage mirror). */
export async function addProviderCard(
  body: Record<string, unknown>,
  t: (key: string) => string,
): Promise<{ ok: boolean; error: string }> {
  try {
    const res = await api.addProvider(body);
    if (!res.success) {
      return { ok: false, error: res.detail || t('settings.provider.failed') };
    }
    return { ok: true, error: '' };
  } catch (err: unknown) {
    return { ok: false, error: providerErrorMessage(err, t) };
  }
}

/** LEGACY (see file header): URL builder for ProviderSettings' raw
 * endpoints. getApiBaseUrl() is called per invocation so local/cloud
 * switches always resolve to the right host. */
export function providerApiUrl(path: string = ''): string {
  return `${getApiBaseUrl()}/api/providers${path}`;
}

/** LEGACY (see file header): fetch with identity headers, delegating to
 * lib/authHeaders' canonical parse point (three older hand-parsers still
 * exist in api.ts / arenaLanding.ts / artifactsApi.ts — do not add
 * more). Headers are set one by one ON TOP of init.headers so the
 * caller's Content-Type survives. No session-death guard — which is why
 * new code goes through ApiClient instead. */
export function authFetch(
  input: RequestInfo | URL,
  init?: RequestInit,
): Promise<Response> {
  const headers = new Headers(init?.headers);
  Object.entries(getAuthHeaders()).forEach(([k, v]) => headers.set(k, v));
  return fetch(input, { ...init, headers });
}
