/**
 * @file_name: builderSession.ts
 * @author: NetMind.AI
 * @date: 2026-09-03
 * @description: One consume-once flag: "the next message this agent sends
 * should carry the Agent Builder instruction".
 *
 * The studio cannot send its instruction on arrival, because the instruction
 * has to wrap the user's OWN first sentence — that sentence is what the
 * builder is meant to act on, and it does not exist until they type it (or
 * click a suggested prompt). So the choose page marks the agent, and the
 * composer's submit path consumes the mark exactly once.
 *
 * sessionStorage rather than router state or a store field, for three
 * reasons: it survives the reload that router state does not, it is scoped
 * to the tab so two studio sessions in two tabs cannot steal each other's
 * mark, and it needs no change to chatStore — v0's whole point is to avoid
 * touching load-bearing paths.
 *
 * Consume-once is the important half of the contract. If the mark leaked
 * into later turns, every message the user sent would re-send the full
 * instruction, which both wastes context and re-teaches a model that has
 * already been told.
 */

const KEY_PREFIX = 'nn.builderPending.';

function storage(): Storage | null {
  try {
    // Private-mode Safari throws on access, and SSR/tests may have no window.
    return typeof window === 'undefined' ? null : window.sessionStorage;
  } catch {
    return null;
  }
}

/** Mark the agent so its next outgoing message carries the instruction. */
export function markBuilderPending(agentId: string): void {
  if (!agentId) return;
  storage()?.setItem(`${KEY_PREFIX}${agentId}`, '1');
}

/**
 * Read and clear the mark.
 *
 * Returns true at most once per mark: the caller is about to build the
 * wrapped message, and a second call must not wrap again.
 */
export function takeBuilderPending(agentId: string): boolean {
  if (!agentId) return false;
  const store = storage();
  if (!store) return false;
  const key = `${KEY_PREFIX}${agentId}`;
  const present = store.getItem(key) !== null;
  if (present) store.removeItem(key);
  return present;
}

/** Drop the mark without consuming it as a send — used when the user leaves
 *  the studio flow without ever sending anything. */
export function clearBuilderPending(agentId: string): void {
  if (!agentId) return;
  storage()?.removeItem(`${KEY_PREFIX}${agentId}`);
}
