/**
 * @file_name: authHeaders.ts
 * @description: Identity headers for outbound requests, read straight from
 * localStorage.
 *
 * Extracted from `lib/api.ts` so the session guard can probe
 * `GET /api/auth/session` without importing the API client (which imports
 * the guard — a cycle). Reading localStorage rather than `configStore` is
 * the same deliberate constraint api.ts has always lived under: importing
 * the store here would create `api → configStore → api`.
 */

/**
 * Two headers, mutually compatible:
 *   - `Authorization: Bearer <jwt>` — cloud mode, signed identity
 *   - `X-User-Id: <user_id>`        — local mode, unsigned identity
 *
 * Both are sent whenever available; the backend's auth_middleware decides
 * which one to trust (cloud: JWT only, X-User-Id ignored as defence in
 * depth; local: X-User-Id only, there is no signing key).
 */
export function getAuthHeaders(): Record<string, string> {
  const headers: Record<string, string> = {};
  try {
    const raw = localStorage.getItem('narra-nexus-config');
    if (raw) {
      const config = JSON.parse(raw);
      const token = config?.state?.token;
      const userId = config?.state?.userId;
      if (token) headers['Authorization'] = `Bearer ${token}`;
      if (userId) headers['X-User-Id'] = userId;
    }
  } catch {
    /* localStorage may be unavailable / disabled — fall through */
  }
  return headers;
}

/** The raw session JWT, or '' when there is none (local mode / logged out). */
export function getSessionToken(): string {
  try {
    const raw = localStorage.getItem('narra-nexus-config');
    if (!raw) return '';
    const token = JSON.parse(raw)?.state?.token;
    return typeof token === 'string' ? token : '';
  } catch {
    return '';
  }
}
