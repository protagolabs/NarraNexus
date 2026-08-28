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
 * Would the chip row render anything?
 *
 * Exported so a caller can collapse its whole container up front rather than
 * leave an empty strip around a component that returned null — the card's
 * RunMeta needs exactly that ("no chips AND no input/output → render nothing").
 * Predicate and render share one set of conditions so the two cannot disagree.
 */
export function hasRunStats(meta: EventLogMeta): boolean {
  return (
    meta.state === 'failed' ||
    meta.state === 'cancelled' ||
    meta.duration_seconds != null ||
    meta.total_cost_usd != null ||
    inputSideTokens(meta) > 0 ||
    meta.output_tokens > 0 ||
    meta.models.length > 0
  );
}
