/**
 * @file_name: studioStore.ts
 * @author: NetMind.AI
 * @date: 2026-09-03
 * @description: Reactive state of the creation studio, per agent.
 *
 * Three things, all keyed by agent id:
 *   - whether the studio is OPEN on that agent (every outgoing message gets
 *     the builder envelope, the drawer offers the configuration panel);
 *   - the skills / channels the conversation RECOMMENDED and the user has not
 *     acted on yet;
 *   - the last error from a model-driven write, so the panel can show it.
 *
 * Why a store and not sessionStorage reads at render time (the first cut):
 * a sessionStorage read has no subscribers. The panel only re-rendered when
 * name or awareness changed, so a turn that recommended a skill and changed
 * no text left the "From the conversation" section empty; closing the drawer
 * could not end the studio because nothing would have re-rendered the chat's
 * encoder; and the write errors `useStudioTurn` collected had no reader at
 * all. One reactive source fixes all three. `lib/builderSession` is now only
 * the persistence behind it — hydrated once here, written through on change.
 *
 * The flag is PERSISTENT, not one-shot: the instruction has to ride along on
 * every turn (the config envelope is what makes the user's panel edits
 * authoritative), and a model told once at turn 1 reliably stops emitting the
 * draft block a few turns later. Reads never consume it.
 *
 * Leaving the studio clears the flag, the recommendations and the error. There
 * is no "discard": the panel writes to the real agent as the conversation
 * goes, so by the time the user leaves there is nothing to roll back.
 */
import { create } from 'zustand';
import {
  loadStudioSession,
  persistRecommendations,
  persistStudioFlag,
  type StudioRecommendations,
} from '@/lib/builderSession';

const EMPTY_RECOMMENDATIONS: StudioRecommendations = { skill_ids: [], channels: [] };

interface StudioState {
  open: Record<string, true>;
  recommendations: Record<string, StudioRecommendations>;
  applyError: Record<string, string>;

  openStudio: (agentId: string) => void;
  /** Leave the studio on this agent. Configuration already written stays. */
  closeStudio: (agentId: string) => void;
  setRecommendations: (agentId: string, rec: StudioRecommendations) => void;
  setApplyError: (agentId: string, error: string | null) => void;
}

export const useStudioStore = create<StudioState>((set) => {
  const stored = loadStudioSession();
  return {
    open: stored.open,
    recommendations: stored.recommendations,
    applyError: {},

    openStudio: (agentId) => {
      if (!agentId) return;
      persistStudioFlag(agentId, true);
      set((s) => ({ open: { ...s.open, [agentId]: true } }));
    },

    closeStudio: (agentId) => {
      if (!agentId) return;
      persistStudioFlag(agentId, false);
      persistRecommendations(agentId, null);
      set((s) => {
        const open = { ...s.open };
        const recommendations = { ...s.recommendations };
        const applyError = { ...s.applyError };
        delete open[agentId];
        delete recommendations[agentId];
        delete applyError[agentId];
        return { open, recommendations, applyError };
      });
    },

    setRecommendations: (agentId, rec) => {
      if (!agentId) return;
      persistRecommendations(agentId, rec);
      set((s) => ({ recommendations: { ...s.recommendations, [agentId]: rec } }));
    },

    setApplyError: (agentId, error) => {
      if (!agentId) return;
      set((s) => {
        const applyError = { ...s.applyError };
        if (error) applyError[agentId] = error;
        else delete applyError[agentId];
        return { applyError };
      });
    },
  };
});

/** Selector: is the studio open on this agent. Never consumes the flag. */
export function selectStudioOpen(agentId: string | null | undefined) {
  return (s: StudioState): boolean => !!agentId && s.open[agentId] === true;
}

/** Selector: this agent's pending recommendations (a stable empty when none). */
export function selectRecommendations(agentId: string | null | undefined) {
  return (s: StudioState): StudioRecommendations =>
    (agentId && s.recommendations[agentId]) || EMPTY_RECOMMENDATIONS;
}

/** Non-hook read for callbacks that run outside render. */
export function isStudioOpen(agentId: string | null | undefined): boolean {
  return selectStudioOpen(agentId)(useStudioStore.getState());
}
