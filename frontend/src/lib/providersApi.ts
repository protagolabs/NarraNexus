/**
 * @file_name: providersApi.ts
 * @author: NarraNexus
 * @date: 2026-08-28
 * @description: Shared authenticated fetch + URL builder for /api/providers.
 *
 * Extracted from ProviderSettings so SubscriptionConnect (and SetupPage's
 * thin addProvider wrapper) reuse the exact same identity semantics instead
 * of growing copies. Identity travels in HEADERS ONLY (X-User-Id in local,
 * JWT Bearer in cloud) — never the query string; the backend dropped the
 * "first user in users table" fallback on 2026-05-18 and 401s when the
 * headers are missing, which is the correct failure.
 */
import { getApiBaseUrl } from '@/stores/runtimeStore';

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
 * Reads the persisted config store snapshot directly from localStorage —
 * NOT the zustand hook — so plain async handlers (and non-React callers)
 * can use it. Corrupt or absent storage degrades to an unauthenticated
 * request; the backend 401s if the request actually needed identity. */
export function authFetch(
  input: RequestInfo | URL,
  init?: RequestInit,
): Promise<Response> {
  const headers = new Headers(init?.headers);
  try {
    const raw = localStorage.getItem('narra-nexus-config');
    if (raw) {
      const state = JSON.parse(raw)?.state || {};
      if (state.token) headers.set('Authorization', `Bearer ${state.token}`);
      if (state.userId) headers.set('X-User-Id', state.userId);
    }
  } catch {
    // Degrade to unauthenticated — see docstring.
  }
  return fetch(input, { ...init, headers });
}
