/**
 * @file NarraUsageSection.tsx
 * @author NarraNexus
 * @date 2026-08-19
 * @description "How much of this did NarraNexus actually use" — the platform's
 * own ledger, rendered next to the NetMind balance it is constantly confused
 * with.
 *
 * Why this exists
 * ---------------
 * The balance and the activity list above it come from NetMind's FINANCE
 * domain (`/v1/finance/user-fee-info`, `/v1/finance/records`), which is scoped
 * to the NETMIND ACCOUNT — every product that account touches, not just this
 * one. Spend the same account on another platform and the number in this card
 * drops, with nothing on screen saying why. Reported 2026-08-19 (P2, "usage
 * shows model/api usage, not narra usage").
 *
 * NarraNexus does keep its own per-call ledger (`cost_records`, written by
 * utils/cost_tracker.py, read via GET /api/agents/_all/costs scoped to the
 * agents the viewer owns). It was only ever surfaced in the chat header's
 * token popover — nowhere near the balance. This section closes that gap.
 *
 * The dollar figure is deliberately an ESTIMATE
 * --------------------------------------------
 * `cost_records.total_cost_usd` prices tokens at litellm's published LIST rate;
 * NetMind is an aggregator and does not invoice at the vendor's direct rate
 * (see utils/model_pricing.py, "LIST price, not invoice price"). So this number
 * answers "roughly how much did NarraNexus consume" and must never be presented
 * as reconciling against the balance above. Hence `≈` plus a stated caveat —
 * and hence tokens, which ARE measured exactly, carry the display.
 *
 * Failure posture: this is an explanatory add-on to a card about money. A
 * broken/empty ledger renders NOTHING rather than an error or a "$0.00" (which
 * reads as "free" instead of "unknown"). It must not be able to take the
 * billing card down with it.
 */

import { useEffect, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { api } from '@/lib/api';
import { formatCost, formatTokens } from '@/lib/tokenFormat';
import type { CostModelBreakdown, CostSummary } from '@/types';

/** Match the NetMind finance view above, which is month-shaped. */
const WINDOW_DAYS = 30;
/** Enough to show where the money goes without turning the card into a report. */
const MAX_MODELS = 4;

/** Drop the date suffix so `claude-opus-5-2026-05-01` fits a settings row. */
function shortModelName(model: string): string {
  return model.replace(/-\d{4}-?\d{2}-?\d{2}$/, '').replace(/-\d{8}$/, '');
}

/**
 * Every bucket the model read, plus what it wrote.
 *
 * input_tokens is only the FULL-RATE bucket; cache reads (0.1x) and cache
 * writes (1.25x) are separate columns. Summing only the first under-reports a
 * cache-warm month by an order of magnitude. `?? 0` guards a backend build
 * predating those fields — undefined in a sum renders "NaN".
 */
function bucketTotal(d: CostModelBreakdown | CostSummary): number {
  if ('total_input_tokens' in d) {
    return (
      d.total_input_tokens +
      (d.total_cache_read_tokens ?? 0) +
      (d.total_cache_creation_tokens ?? 0) +
      d.total_output_tokens
    );
  }
  return (
    d.input_tokens +
    (d.cache_read_tokens ?? 0) +
    (d.cache_creation_tokens ?? 0) +
    d.output_tokens
  );
}

export function NarraUsageSection() {
  const { t } = useTranslation();
  const [summary, setSummary] = useState<CostSummary | null>(null);
  const mounted = useRef(true);

  useEffect(() => {
    mounted.current = true;
    void (async () => {
      try {
        const res = await api.getCosts('_all', WINDOW_DAYS);
        if (mounted.current) setSummary(res.summary ?? null);
      } catch {
        // Silent by design — see the failure posture note in the file header.
        // Nothing here is actionable by the user, and an error line inside a
        // billing card reads as "your money is broken".
      }
    })();
    return () => {
      mounted.current = false;
    };
  }, []);

  if (!summary) return null;
  const totalTokens = bucketTotal(summary);
  // No usage yet is not a fact worth a heading. It is also the state a brand
  // new account is in, where an empty "0" block is pure noise.
  if (totalTokens <= 0) return null;

  const models = Object.entries(summary.by_model ?? {})
    .sort(([, a], [, b]) => bucketTotal(b) - bucketTotal(a))
    .slice(0, MAX_MODELS);
  // > 0 only: an unpriced model books $0, and "$0.00" would claim it was free
  // rather than admit we don't know the rate.
  const costText = summary.total_cost_usd > 0 ? formatCost(summary.total_cost_usd) : null;

  return (
    <div className="pt-3 border-t border-[var(--border-subtle)] space-y-2">
      <div className="flex items-baseline justify-between gap-2">
        <span className="text-xs font-medium text-[var(--text-secondary)]">
          {t('settings.netmind.narraUsageTitle', 'Used by NarraNexus · last {{days}} days', {
            days: WINDOW_DAYS,
          })}
        </span>
        <span className="font-mono tabular-nums text-sm text-[var(--text-primary)]">
          {formatTokens(totalTokens)}
          <span className="ml-1 text-[11px] text-[var(--text-tertiary)]">
            {t('settings.netmind.narraUsageTokens', 'tokens')}
          </span>
        </span>
      </div>

      {models.length > 0 && (
        <ul className="space-y-1">
          {models.map(([model, data]) => (
            <li
              key={model}
              data-testid="narra-usage-model"
              className="flex items-center justify-between gap-2 text-xs text-[var(--text-secondary)]"
            >
              <span className="truncate" title={model}>
                {shortModelName(model)}
              </span>
              <span className="flex items-center gap-2 shrink-0">
                <span className="text-[11px] text-[var(--text-tertiary)]">
                  ×{data.call_count}
                </span>
                <span className="font-mono tabular-nums text-[var(--text-primary)] min-w-[52px] text-right">
                  {formatTokens(bucketTotal(data))}
                </span>
              </span>
            </li>
          ))}
        </ul>
      )}

      {costText && (
        <p className="text-[11px] text-[var(--text-tertiary)] leading-relaxed">
          {t(
            'settings.netmind.narraUsageEstimate',
            '≈ {{cost}} — estimated from public list prices, so it will not match your NetMind statement exactly.',
            { cost: costText },
          )}
        </p>
      )}
    </div>
  );
}
