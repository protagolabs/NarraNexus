/**
 * @file runStats.ts
 * @author NarraNexus
 * @date 2026-08-28
 * @description Non-component helpers behind the run-stats chip row: how a
 * duration is formatted, and whether a turn has any stats worth a row at all.
 *
 * Split out of components/chat/RunStatChips.tsx purely so that file exports
 * components only (react-refresh/only-export-components). The token and USD
 * rules deliberately do NOT live here — they are shared with the cost popover
 * and the account usage panel, and stay in `lib/tokenFormat`.
 */

import { inputSideTokens } from './tokenFormat';
import type { EventLogMeta } from '@/types/api';

/** 90 → "1m 30s", 42 → "42s", 3900 → "1h 5m". */
export function formatDuration(seconds: number): string {
  if (seconds < 60) return `${Math.round(seconds)}s`;
  const m = Math.floor(seconds / 60);
  const s = Math.round(seconds % 60);
  if (m < 60) return s > 0 ? `${m}m ${s}s` : `${m}m`;
  const h = Math.floor(m / 60);
  const rm = m % 60;
  return rm > 0 ? `${h}h ${rm}m` : `${h}h`;
}

/**
 * Is there a price worth showing?
 *
 * `> 0`, not `!= null`. A booked 0 is not a cheap turn — it is an UNPRICED
 * one: `model_pricing.price_for` returns None for any model id litellm's
 * table doesn't know, `calculate_cost` then books 0, and that is the majority
 * of rows on a local install (1837 of 2384 when this was written, all of them
 * the main DeepSeek / GLM ids). Gating on `!= null` sent that 0 into
 * `formatCost`, whose own contract says callers gate on > 0, and it renders
 * sub-hundredth-of-a-cent values as "<$0.0001" — so "we don't know the rate"
 * appeared on screen as "it cost a little something".
 *
 * Same rule as the backend's `_build_event_meta`, which leaves
 * `total_cost_usd` None when there are no ledger rows precisely so the UI can
 * hide the chip instead of showing a misleading $0. No rows and no price are
 * the same situation to a reader.
 */
export function hasCostToShow(meta: EventLogMeta): boolean {
  return meta.total_cost_usd != null && meta.total_cost_usd > 0;
}

/**
 * Would the chip row render anything?
 *
 * Exported so a caller can collapse its whole container up front rather than
 * leave an empty strip around a component that returned null — the card's
 * RunMeta needs exactly that ("no chips AND no input/output → render nothing").
 *
 * Predicate and render must agree. They are two hand-written copies of the
 * same conditions today; the shared helpers (`hasCostToShow`, `hasTokens`)
 * exist so the ones that have already been gotten wrong stay in one place.
 * Adding a chip means touching both — a mismatch shows up as a turn whose
 * only datum is the new chip rendering no row at all.
 */
export function hasTokens(meta: EventLogMeta): boolean {
  return inputSideTokens(meta) > 0 || meta.output_tokens > 0;
}

export function hasRunStats(meta: EventLogMeta): boolean {
  return (
    meta.state === 'failed' ||
    meta.state === 'cancelled' ||
    meta.duration_seconds != null ||
    hasCostToShow(meta) ||
    hasTokens(meta) ||
    meta.models.length > 0
  );
}
