/**
 * @file_name: providersApi.ts
 * @author: NarraNexus
 * @date: 2026-08-28
 * @description: Shared plumbing for /api/providers callers.
 *
 * Exists because the SubscriptionConnect extraction gave the provider
 * endpoints three frontend callers (ProviderSettings, SubscriptionConnect,
 * SetupPage). What is shared here:
 *   - authFetch — DELEGATES identity to lib/authHeaders (the canonical
 *     parse point; an earlier draft hand-copied the localStorage parse and
 *     was weaker — no string guards — which is exactly how the 2026-05-18
 *     cross-user identity bug family starts. Three legacy hand-parsers
 *     still exist elsewhere: api.ts, arenaLanding.ts, artifactsApi.ts —
 *     do not add a fourth).
 *   - postProvider — the POST /api/providers body/error contract, so the
 *     `detail` field mapping cannot drift between callers.
 *   - ProviderRow — the one row shape all three callers read.
 */
import { getAuthHeaders } from '@/lib/authHeaders';
import { getApiBaseUrl } from '@/stores/runtimeStore';

/** The provider-row fields the frontend reads. The backend row carries
 * more; add fields here (one place) as consumers need them. */
export interface ProviderRow {
  provider_id: string;
  name?: string;
  source?: string;
  protocol?: string;
  auth_type?: string;
}

/** Build a provider API URL against the CURRENT backend host.
 *
 * getApiBaseUrl() is called per invocation (never captured at import
 * time), so mode switches between local and cloud always resolve to the
 * right host without any re-mount. */
export function providerApiUrl(path: string = ''): string {
  return `${getApiBaseUrl()}/api/providers${path}`;
}

/** fetch with the identity headers this backend requires.
 *
 * Identity comes from lib/authHeaders' single parse point. Headers are
 * set one by one ON TOP of init.headers — `new Headers(getAuthHeaders())`
 * would drop the caller's Content-Type. */
export function authFetch(
  input: RequestInfo | URL,
  init?: RequestInit,
): Promise<Response> {
  const headers = new Headers(init?.headers);
  Object.entries(getAuthHeaders()).forEach(([k, v]) => headers.set(k, v));
  return fetch(input, { ...init, headers });
}

/** POST /api/providers with the shared body/error contract.
 *
 * Returns the outcome only — NO side effects: the callers' refresh
 * choreography differs (ProviderSettings refetches its own list,
 * SetupPage re-probes and bumps the refresh token) and must stay
 * theirs. On failure, `detail` is `null` for a network-level failure
 * and the backend's reason string otherwise ('' when it gave none);
 * turn it into user copy with providerErrorMessage below. */
export async function postProvider(
  body: Record<string, unknown>,
): Promise<{ ok: boolean; detail: string | null }> {
  try {
    const res = await authFetch(providerApiUrl(), {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    }).then((r) => r.json());
    if (!res.success) return { ok: false, detail: String(res.detail || '') };
    return { ok: true, detail: '' };
  } catch {
    return { ok: false, detail: null };
  }
}

/** Map a failed postProvider result to user-facing copy — one mapping
 * for every caller, so /setup and Settings never word the same failure
 * differently. A non-empty backend `detail` is shown verbatim (it
 * carries the actual reason); the two fallbacks are i18n keys. */
export function providerErrorMessage(
  detail: string | null,
  t: (key: string) => string,
): string {
  if (detail === null) return t('settings.provider.networkError');
  return detail || t('settings.provider.failed');
}
