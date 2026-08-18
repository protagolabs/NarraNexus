/**
 * @file NetmindRenewControls.tsx
 * @author NarraNexus
 * @date 2026-08-19
 * @description Buy N more months of Pro on a one-time rail (Alipay / WeChat).
 *
 * Why this is not the subscribe button with a number next to it: a one-time
 * purchase is a different product from a card subscription, not a parameter of
 * it. It does not renew, so the thing that keeps someone Pro is THIS control
 * being used again before the period ends — which is why the copy leads with
 * "does not auto-renew" and anchors on a real date rather than a bare month
 * count. That date is the one the purchase EXTENDS FROM — never a computed
 * "covered until"; see the comment on `extendsFrom` for why we refuse to do
 * that arithmetic ourselves.
 *
 * Card is deliberately absent from the rail choice here, and that is a
 * capability fact rather than the region-based hiding PaymentMethodChoice
 * refuses to do: while a one-time subscription is live, upstream rejects a card
 * subscribe with "Already subscribed to Pro." (measured on dev 2026-08-19), so
 * offering it would be offering a guaranteed failure.
 *
 * Presentational. The purchase, its double-submit guard and the session poll
 * stay in NetmindAccountPanel with the rest of the money handlers.
 */

import { useTranslation } from 'react-i18next';
import { Button } from '@/components/ui';
import { PaymentMethodChoice } from './PaymentMethodChoice';
import { formatDate, money } from './netmindFormat';
import type { SubscribePaymentMethod } from '@/types';

// 1 and 12 are the upstream bounds; the middle values are the quarters people
// actually think in. A grid rather than a number input: the bound then cannot
// be violated at all, so the upstream `invalid_months` 400 is unreachable.
const MONTH_CHOICES = [1, 2, 3, 6, 9, 12];

interface NetmindRenewControlsProps {
  months: number;
  onChangeMonths: (n: number) => void;
  payMethod: Extract<SubscribePaymentMethod, 'alipay' | 'wechat'>;
  onChangePayMethod: (m: Extract<SubscribePaymentMethod, 'alipay' | 'wechat'>) => void;
  /** Price of ONE month in USD, or null when the plan catalog hasn't loaded. */
  monthlyPriceUsd: number | null;
  /** CNY conversion of the current total — WeChat only, null while unknown. */
  chargeAmountCny: string | null;
  /** Unix seconds the current period ends; the purchase extends from there. */
  currentPeriodEnd?: number;
  /** This purchase's own progress — never the top-up's. Drives the NARRATIVE
   *  (waiting / done / failed) and nothing else. */
  state: 'idle' | 'processing' | 'success' | 'failed';
  /** Any money action is in flight, whichever control started it. Separate from
   *  `state` on purpose: the submit guard is shared with the top-up (two
   *  checkouts at once is not a state we want), so a control that is NOT the
   *  narrator must still be disabled — otherwise its button stays clickable,
   *  hits the guard's early return, and nothing at all happens on screen. */
  busy: boolean;
  error: string | null;
  onPay: () => void;
}

export function NetmindRenewControls({
  months,
  onChangeMonths,
  payMethod,
  onChangePayMethod,
  monthlyPriceUsd,
  chargeAmountCny,
  currentPeriodEnd,
  state,
  busy,
  error,
  onPay,
}: NetmindRenewControlsProps) {
  const { t } = useTranslation();
  const total = monthlyPriceUsd != null ? monthlyPriceUsd * months : null;

  // The date the purchase EXTENDS FROM — never a locally computed "covered
  // until". A month is not 30 days: upstream defines the period (dev bills a
  // "month" as `2day`, and a real calendar month is 28-31), so any arithmetic
  // here invents a date the server owns. It showed 2026-09-23 next to a plan
  // row saying 2026-08-24, and on prod it would have been quietly wrong by a
  // day or three every month instead — the worse failure, because nobody
  // notices it. Same rule the CNY conversion follows: quote what the server
  // said, never a number we made up.
  //
  // `formatDate` (not toLocaleDateString) so this reads identically to the
  // plan row it sits under.
  const extendsFrom = currentPeriodEnd != null ? formatDate(currentPeriodEnd) : null;

  return (
    <div className="space-y-3">
      <PaymentMethodChoice
        value={payMethod}
        hideCard
        onChange={onChangePayMethod}
        disabled={busy}
      />

      <div className="space-y-1.5">
        <div className="text-xs text-[var(--text-tertiary)]">
          {t('settings.netmind.renewMonthsLabel', 'How many months')}
        </div>
        <div className="grid grid-cols-6 gap-1.5">
          {MONTH_CHOICES.map((n) => (
            <button
              key={n}
              type="button"
              onClick={() => onChangeMonths(n)}
              disabled={busy}
              aria-pressed={months === n}
              className={`h-8 rounded-[var(--radius-sm)] border text-[13px] tabular-nums transition-colors disabled:opacity-50 ${
                months === n
                  ? 'border-[var(--nm-ink)] text-[var(--text-primary)] bg-[var(--nm-ink)]/[0.06] font-semibold'
                  : 'border-[var(--border-default)] text-[var(--text-secondary)] hover:border-[var(--border-strong)]'
              }`}
            >
              {n}
            </button>
          ))}
        </div>
      </div>

      {total != null && (
        <div className="flex items-baseline justify-between gap-3 px-3 py-2.5 rounded-[var(--radius-sm)] bg-[var(--nm-paper-warm)] border border-[var(--nm-hairline)]">
          <div>
            <div className="text-xs text-[var(--text-tertiary)]">
              {t('settings.netmind.renewTotalLabel', '{{count}}-month total', { count: months })}
            </div>
            <div className="text-xl font-semibold tabular-nums text-[var(--text-primary)]">
              ${money(total)}
            </div>
          </div>
          <div className="text-[11px] text-[var(--text-tertiary)] text-right tabular-nums">
            {/* No bulk discount exists, so it is not implied: the arithmetic is
                shown instead of a "save X%" that would be a lie. */}
            <div>${money(monthlyPriceUsd as number)} × {months}</div>
            {chargeAmountCny && <div>≈ ¥{chargeAmountCny}</div>}
          </div>
        </div>
      )}

      <p className="text-xs text-[var(--color-warning)]">
        {t('settings.netmind.renewNoAutoRenew',
          'One-time purchase — this does NOT auto-renew. Buy again before it ends to stay on Pro.')}
      </p>

      <div className="flex items-center justify-between gap-3">
        <p className="text-[11px] text-[var(--text-tertiary)] flex-1">
          {extendsFrom
            ? t('settings.netmind.renewExtendsFrom',
                'Added on top of your current end date, {{date}}.', { date: extendsFrom })
            : ''}
        </p>
        <Button variant="accent" size="sm" onClick={onPay} disabled={busy || total == null}>
          {busy
            ? t('settings.netmind.working', 'Working…')
            : t('settings.netmind.renewPay', 'Pay ${{total}}', { total: total != null ? money(total) : '' })}
        </Button>
      </div>

      {state === 'processing' && (
        <p className="text-xs text-[var(--text-tertiary)]">
          {t('settings.netmind.renewProcessing',
            'Waiting for payment… complete it in the opened window; your plan updates automatically.')}
        </p>
      )}
      {state === 'success' && (
        <p className="text-xs text-[var(--color-success)]">
          {t('settings.netmind.renewSuccess', 'Pro extended — new end date shown above.')}
        </p>
      )}
      {error && <p className="text-xs text-[var(--color-error)]">{error}</p>}
    </div>
  );
}
