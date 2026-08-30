/**
 * useNarrationTier — "should NexusPower's narration render at the progress
 * tier right now?"
 *
 * One reader for the `interimNarration` display preference (design A′). It
 * exists because three surfaces need the same answer — TurnTimeline, and the
 * two panels that feed ProcessEventRows — and three copies of
 * `useUIStore((s) => s.interimNarration)` is three places to miss when the
 * default or the source of the preference changes.
 *
 * ProcessEventRows deliberately does NOT call this: it is a shared row
 * renderer used by two panels, and a hidden global input in a shared render
 * component is how you get "I passed the right events and it still looks
 * wrong". Its callers resolve the preference and pass it down.
 */
import { useUIStore } from '@/stores/uiStore';

export function useNarrationTier(): boolean {
  return useUIStore((s) => s.interimNarration);
}
