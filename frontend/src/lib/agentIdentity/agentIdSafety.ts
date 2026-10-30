/**
 * @file_name: agentIdSafety.ts
 * @author: Bin Liang
 * @date: 2026-10-30
 * @description: Whether a string is usable as an agent id at all — the frontend twin of the backend's `safe_id_segment`.
 *
 * The backend refuses an agent id that is not ONE path segment (separators,
 * NUL, dot-led names such as `..`; workspace_paths.safe_id_segment) and logs
 * a signed tenant-boundary violation that the edge monitor bans on when the
 * same account sends different such values. A crafted link
 * (`/app/agents/.x`) would otherwise make the victim's own browser send them,
 * so ids that cannot be real are dropped before any request carries them.
 * `%` is refused as well: no real id contains it, and a second decoding layer
 * is how `..` gets smuggled past a single decode. Legacy ids with spaces or
 * odd shapes stay valid — the rule is about path safety, not id format.
 */

export function isSafeAgentId(id: string): boolean {
  if (!id) return false;
  if (id.startsWith('.')) return false;
  if (id.includes('\u0000')) return false;
  return !/[/\\%]/.test(id);
}

/** Decode a route param into an agent id, or '' when it is malformed or unsafe. */
export function agentIdFromRoute(param: string | undefined): string {
  if (!param) return '';
  let decoded: string;
  try {
    decoded = decodeURIComponent(param);
  } catch {
    return '';
  }
  return isSafeAgentId(decoded) ? decoded : '';
}
