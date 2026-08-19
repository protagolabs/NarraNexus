/**
 * @file tokenFormat.ts
 * @author NarraNexus
 * @date 2026-08-19
 * @description Shared rendering rules for every LLM-usage surface: token counts,
 * USD amounts, and the model label.
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

/**
 * Display label for a `by_model` key from GET /api/agents/{id}/costs.
 *
 * That endpoint does NOT key by model id. It buckets every row into exactly two
 * synthetic keys by call_type (backend/routes/agents/cost.py): `__main_model__`
 * for agent_loop, `__helper_model__` for everything else. Rendering the key
 * verbatim puts a raw `__main_model__` on the user's screen — which is what
 * shipped to the account page before a live check caught it.
 *
 * The date-suffix strip below is therefore unreachable for that endpoint today;
 * it stays as the sane default for any caller handed a real model id.
 */
export function shortModelName(
  model: string,
  labels: { main: string; helper: string },
): string {
  if (model === '__main_model__') return labels.main;
  if (model === '__helper_model__') return labels.helper;
  return model.replace(/-\d{4}-?\d{2}-?\d{2}$/, '').replace(/-\d{8}$/, '');
}
