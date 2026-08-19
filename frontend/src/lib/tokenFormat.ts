/**
 * @file tokenFormat.ts
 * @author NarraNexus
 * @date 2026-08-19
 * @description Shared token-count / USD formatting for every LLM-usage surface.
 *
 * Extracted from CostPopover when a second usage surface (the account page's
 * NarraNexus-usage section) needed the same two functions. Two independent
 * copies of "how do we render a token count" drift, and they drift silently —
 * the same week's usage would read 1.2M on one screen and 1.23M on another,
 * and the reader has no way to tell which one is rounded.
 *
 * NOTE: InnerThoughtCard.tsx still carries a third copy with slightly
 * different rules (1 decimal at the M scale instead of 2). Folding it in
 * changes what that card renders and what its tests assert, so it is tracked
 * separately rather than smuggled into a billing-copy fix.
 */

/** Format a token count: 980 → "980", 12345 → "12.3k", 2_400_000 → "2.40M". */
export function formatTokens(n: number): string {
  if (n < 1000) return n.toString();
  if (n < 1_000_000) return `${(n / 1000).toFixed(1)}k`;
  return `${(n / 1_000_000).toFixed(2)}M`;
}

/**
 * Format USD so a real amount never renders as a zero.
 *
 * toFixed(4) still prints "$0.0000" below a hundredth of a cent — and an
 * embedding-heavy day lands there. That is the same failure these panels avoid
 * everywhere else by hiding cost when it is 0: a displayed zero reads as
 * "free", not as "too small to show". Callers already gate on > 0, so anything
 * reaching here is genuinely non-zero and says so.
 */
export function formatCost(n: number): string {
  if (n >= 0.01) return `$${n.toFixed(2)}`;
  if (n >= 0.0001) return `$${n.toFixed(4)}`;
  return '<$0.0001';
}
