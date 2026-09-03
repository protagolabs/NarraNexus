/**
 * @file_name: builderSession.ts
 * @author: NetMind.AI
 * @date: 2026-09-03
 * @description: One per-agent flag: "the creation studio is open on this
 * agent".
 *
 * While it is set, two things happen — the configuration panel is the drawer's
 * active tab, and every outgoing message is wrapped with the builder
 * instruction plus the agent's current config (see [[builderProtocol.ts]]).
 *
 * It is a PERSISTENT flag, not a one-shot mark. The instruction has to ride
 * along on every turn, not just the first: the config envelope is what makes
 * the user's manual panel edits authoritative, and a model told once at turn 1
 * reliably stops emitting the draft block a few turns later. Re-stating costs
 * tokens; a silently-stopped draft block costs the whole feature.
 *
 * sessionStorage rather than router state or a store field:
 *   - survives the reload that router state does not;
 *   - scoped to the tab, so two studio sessions in two tabs cannot steal each
 *     other's state;
 *   - needs no change to chatStore, whose submit path is load-bearing.
 *
 * There is no "discard": the panel writes to the real agent as the
 * conversation goes, so by the time the user leaves there is nothing to roll
 * back. Leaving the studio just clears this flag.
 */

const KEY_PREFIX = 'nn.studio.';

function storage(): Storage | null {
  try {
    // Private-mode Safari throws on access; SSR/tests may have no window.
    return typeof window === 'undefined' ? null : window.sessionStorage;
  } catch {
    return null;
  }
}

/** Open the studio on this agent. */
export function openStudio(agentId: string): void {
  if (!agentId) return;
  storage()?.setItem(`${KEY_PREFIX}${agentId}`, '1');
}

/** Leave the studio. Configuration already written to the agent stays. */
export function closeStudio(agentId: string): void {
  if (!agentId) return;
  storage()?.removeItem(`${KEY_PREFIX}${agentId}`);
}

/** Whether the studio is open on this agent. Read-only — never consumes. */
export function isStudioOpen(agentId: string | null | undefined): boolean {
  if (!agentId) return false;
  return storage()?.getItem(`${KEY_PREFIX}${agentId}`) !== null;
}

// ── recommendations ─────────────────────────────────────────────────────

const REC_PREFIX = 'nn.studioRec.';

/**
 * Skills and channels the builder SUGGESTED but nobody installed or bound.
 *
 * These live here, client-side, because they are not agent state — nothing on
 * the server records "an agent was advised to use web-search". Name,
 * description and instructions are different: those are written straight to
 * the agent, so the server is their truth and this module must not shadow
 * them.
 *
 * Kept per agent so the outbound envelope can restate them each turn; without
 * that the model re-suggests the same skill every single reply.
 */
export interface StudioRecommendations {
  skill_ids: string[];
  channels: string[];
}

export function saveRecommendations(agentId: string, rec: StudioRecommendations): void {
  if (!agentId) return;
  try {
    storage()?.setItem(`${REC_PREFIX}${agentId}`, JSON.stringify(rec));
  } catch {
    // Quota or private mode — recommendations are a convenience, never block.
  }
}

export function readRecommendations(agentId: string | null | undefined): StudioRecommendations {
  const empty: StudioRecommendations = { skill_ids: [], channels: [] };
  if (!agentId) return empty;
  const raw = storage()?.getItem(`${REC_PREFIX}${agentId}`);
  if (!raw) return empty;
  try {
    const parsed: unknown = JSON.parse(raw);
    if (!parsed || typeof parsed !== 'object') return empty;
    const rec = parsed as Partial<StudioRecommendations>;
    return {
      skill_ids: Array.isArray(rec.skill_ids) ? rec.skill_ids.filter((s) => typeof s === 'string') : [],
      channels: Array.isArray(rec.channels) ? rec.channels.filter((s) => typeof s === 'string') : [],
    };
  } catch {
    return empty;
  }
}

export function clearRecommendations(agentId: string): void {
  if (!agentId) return;
  storage()?.removeItem(`${REC_PREFIX}${agentId}`);
}
