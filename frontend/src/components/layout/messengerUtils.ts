/**
 * @file_name: messengerUtils.ts
 * @author:
 * @date: 2026-08-25
 * @description: Pure helpers for the sidebar's Messenger row — per-agent and
 * per-team last-message preview + timestamp, and most-recent-first ordering
 * across both. Extracted so both can be unit-tested without mounting
 * MessengerSection.
 */

/** Minimal agent shape the helpers need. */
export interface MessengerAgent {
  agent_id: string;
  last_assistant_preview?: string | null;
  last_assistant_at?: string | null;
  created_at?: string;
}

/** Minimal chat message shape the helpers need. */
export interface MessengerMessage {
  role: string;
  content: string;
  timestamp?: number;
}

export interface MessengerRowMeta {
  /** Last-message preview, whitespace-collapsed and capped at 60 chars. */
  preview: string;
  /** Epoch-ms of that message (0 = nothing to show). */
  timeMs: number;
}

/**
 * Derives the preview + time shown under an agent's name in the Messenger
 * row. Prefers the freshest LOCAL session message (so a row updates the
 * instant you talk to that agent, before the next agents refresh) and falls
 * back to the server-persisted last-assistant preview otherwise.
 */
export function computeRowMeta(
  agent: MessengerAgent,
  sessionMessages: MessengerMessage[],
): MessengerRowMeta {
  let sessionLast: MessengerMessage | null = null;
  for (let i = sessionMessages.length - 1; i >= 0; i--) {
    if (sessionMessages[i].role === 'assistant' && sessionMessages[i].content) {
      sessionLast = sessionMessages[i];
      break;
    }
  }

  const serverPreview = agent.last_assistant_preview || '';
  const serverAtMs = isoToMs(agent.last_assistant_at);
  const sessionAtMs = sessionLast?.timestamp ?? 0;

  if (sessionLast && sessionAtMs >= serverAtMs) {
    return { preview: normalize(sessionLast.content), timeMs: sessionAtMs };
  }
  if (serverPreview) {
    return { preview: normalize(serverPreview), timeMs: serverAtMs };
  }
  return { preview: '', timeMs: 0 };
}

function normalize(text: string): string {
  return text.replace(/\s+/g, ' ').trim().slice(0, 60);
}

/** Parse an ISO timestamp to epoch-ms; 0 for empty/invalid input. */
function isoToMs(iso: string | null | undefined): number {
  if (!iso) return 0;
  const ms = new Date(iso).getTime();
  return Number.isNaN(ms) ? 0 : ms;
}

/** An agent's activity time: newest of its last persisted assistant reply,
 *  the freshest local session message, and its creation time (a floor for
 *  never-chatted agents). */
function agentActivityScore(agent: MessengerAgent, localActivityMs: number): number {
  return Math.max(isoToMs(agent.last_assistant_at), localActivityMs || 0, isoToMs(agent.created_at));
}

/**
 * Sorts agents so the most-recently-active conversation floats to the top.
 * Pure — returns a new array; ties break by agent_id so equal timestamps
 * don't churn row order between renders.
 */
export function sortAgentsByActivity<A extends MessengerAgent>(
  agents: A[],
  localActivityMs: (agentId: string) => number,
): A[] {
  return agents
    .map((agent) => ({ agent, score: agentActivityScore(agent, localActivityMs(agent.agent_id)) }))
    .sort((x, y) => {
      if (x.score !== y.score) return y.score - x.score;
      return x.agent.agent_id < y.agent.agent_id ? -1 : x.agent.agent_id > y.agent.agent_id ? 1 : 0;
    })
    .map((x) => x.agent);
}

/** Minimal team shape the helpers need — flattened out of `TeamWithMembers`
 *  (whose `team_id`/`created_at` sit under `.team`, while `last_message_at`
 *  is a root field) so this module doesn't depend on the teams store's type. */
export interface MessengerTeam {
  team_id: string;
  last_message_at?: string | null;
  created_at?: string | null;
}

/** A team room's activity time: newest of its last qualifying room message
 *  (see `TeamWithMembers.last_message_at`) and its creation time — the same
 *  floor `agentActivityScore` uses for a never-chatted agent. */
function teamActivityScore(team: MessengerTeam): number {
  return Math.max(isoToMs(team.last_message_at), isoToMs(team.created_at));
}

/**
 * Derives the preview + time shown under a team's name in the Messenger row.
 * Unlike `computeRowMeta`, there is no local-session fallback — the sidebar
 * never loads a team's transcript, so the server's last-message fields are
 * the only source.
 */
export function computeTeamRowMeta(team: {
  last_message_preview?: string | null;
  last_message_at?: string | null;
}): MessengerRowMeta {
  if (!team.last_message_preview) return { preview: '', timeMs: 0 };
  return { preview: normalize(team.last_message_preview), timeMs: isoToMs(team.last_message_at) };
}

/** One row in the merged Messenger list — enough to look the real agent or
 *  team back up in its own store. */
export interface MessengerListItem {
  kind: 'agent' | 'team';
  id: string;
}

/**
 * Interleaves agents and teams into a single most-recent-first list. Both
 * are scored on the same clock (`agentActivityScore` / `teamActivityScore`)
 * so a team room that just got a reply outranks an agent chat that has been
 * quiet, and vice versa. Ties break by id, same rationale as
 * `sortAgentsByActivity`.
 */
export function sortMessengerItems(
  agents: MessengerAgent[],
  teams: MessengerTeam[],
  localAgentActivityMs: (agentId: string) => number,
): MessengerListItem[] {
  const scored = [
    ...agents.map((a) => ({
      kind: 'agent' as const,
      id: a.agent_id,
      score: agentActivityScore(a, localAgentActivityMs(a.agent_id)),
    })),
    ...teams.map((t) => ({
      kind: 'team' as const,
      id: t.team_id,
      score: teamActivityScore(t),
    })),
  ];
  scored.sort((x, y) => {
    if (x.score !== y.score) return y.score - x.score;
    return x.id < y.id ? -1 : x.id > y.id ? 1 : 0;
  });
  return scored.map(({ kind, id }) => ({ kind, id }));
}
