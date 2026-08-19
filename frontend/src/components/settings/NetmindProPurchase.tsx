/**
 * @file NetmindProPurchase.tsx
 * @author NarraNexus
 * @date 2026-08-19
 * @description Choose how to pay for Pro — used both to START one and to
 * EXTEND a one-time one. (Was NetmindRenewControls until it grew the card rail;
 * renamed because "renew" stopped describing half of what it does.)
 *
 * The two rails are two different PRODUCTS, not two ways to buy one:
 *
 *   card            a Stripe subscription. Renews itself, can be cancelled.
 *                   A month count is meaningless, so the grid is not shown.
 *   alipay/wechat   a ONE-TIME purchase of N months that simply ends. Nothing
 *                   renews it, which is why the copy leads with that and why
 *                   this control is also the ONLY way such a subscriber stays
 *                   Pro — they have to come back and use it again.
 *
 * So the form changes shape with the rail rather than adding a field to it.
 *
 * `allowCard` is false only while a one-time subscription is live: upstream
 * refuses a card subscribe with "Already subscribed to Pro." then (measured on
 * dev 2026-08-19), so offering it would be offering a guaranteed failure. That
 * is a capability fact, not the region-based hiding PaymentMethodChoice
 * refuses to do.
 *
 * Presentational. The purchase, its double-submit guard and the poll live in
 * NetmindAccountPanel with the rest of the money handlers.
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

interface NetmindProPurchaseProps {
  months: number;
  onChangeMonths: (n: number) => void;
  payMethod: SubscribePaymentMethod;
  onChangePayMethod: (m: SubscribePaymentMethod) => void;
  /** Offer the card rail. False while a one-time subscription is live. */
  allowCard: boolean;
  /** Price of ONE month in USD, or null when the plan catalog hasn't loaded. */
  monthlyPriceUsd: number | null;
  /** CNY conversion of the current total — WeChat only, null while unknown. */
  chargeAmountCny: string | null;
  /** Unix seconds the current period ends — absent for a first purchase. */
  currentPeriodEnd?: number;
  /** This purchase's own progress. Drives the NARRATIVE and nothing else. */
  state: 'idle' | 'processing' | 'success' | 'failed';
  /** ANY money action is in flight, whichever control started it. Separate from
   *  `state`: the submit guards are shared with the other spend controls on
   *  screen, so a control that is not the narrator must still be disabled —
   *  otherwise its button stays clickable, hits a guard's early return, and
   *  nothing at all happens. */
  busy: boolean;
  error: string | null;
  onPay: () => void;
}

export function NetmindProPurchase({
  months,
  onChangeMonths,
  payMethod,
  onChangePayMethod,
  allowCard,
  monthlyPriceUsd,
  chargeAmountCny,
  currentPeriodEnd,
  state,
  busy,
  error,
  onPay,
}: NetmindProPurchaseProps) {
  const { t } = useTranslation();
  const isCard = payMethod === 'stripe';
  const total = monthlyPriceUsd != null ? monthlyPriceUsd * (isCard ? 1 : months) : null;

  // The date the purchase EXTENDS FROM — never a locally computed "covered
  // until". A month is not 30 days: upstream defines the period (dev bills a
  // "month" as `2day`, and a real calendar month is 28-31), so any arithmetic
  // here invents a date the server owns. It once showed 2026-09-23 next to a
  // plan row saying 2026-08-24; on prod it would have been quietly wrong by a
  // day or three every month instead — the worse failure, because nobody
  // notices it. Same rule the CNY conversion follows: quote what the server
  // said, never a number we made up.
  //
  // Absent on a first purchase, which is why the line is conditional.
  const extendsFrom = currentPeriodEnd != null ? formatDate(currentPeriodEnd) : null;

  return (
    <div className="space-y-3">
      {/* A distinct label per instance: this control and the top-up one can sit
          in the same dialog, and two radiogroups both announcing "Payment
          method" leave a screen-reader user unable to tell which one spends
          which money — and a sighted user able to set this one to WeChat and
          assume the other followed. */}
      <PaymentMethodChoice
        value={payMethod}
        {...(allowCard
          ? ({ cardValue: 'stripe' as SubscribePaymentMethod } as const)
          : ({ hideCard: true } as const))}
        label={t('settings.netmind.payMethodLabelPro', 'How to pay for Pro')}
        onChange={onChangePayMethod}
        disabled={busy}
      />

      {!isCard && (
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
      )}

      {total != null && (
        <div className="flex items-baseline justify-between gap-3 px-3 py-2.5 rounded-[var(--radius-sm)] bg-[var(--nm-paper-warm)] border border-[var(--nm-hairline)]">
          <div>
            <div className="text-xs text-[var(--text-tertiary)]">
              {isCard
                ? t('settings.netmind.perMonthLabel', 'Per month')
                : t('settings.netmind.renewTotalLabel', '{{count}}-month total', { count: months })}
            </div>
            <div className="text-xl font-semibold tabular-nums text-[var(--text-primary)]">
              ${money(total)}
            </div>
          </div>
          <div className="text-[11px] text-[var(--text-tertiary)] text-right tabular-nums">
            {/* No bulk discount exists, so it is not implied: the arithmetic is
                shown instead of a "save X%" that would be a lie. Measured — 2
                months bills 38.00, 3 bills 57.00, both exactly 19 x N. */}
            {isCard ? (
              <div>{t('settings.netmind.cardRenews', 'Renews automatically')}</div>
            ) : (
              <div>${money(monthlyPriceUsd as number)} × {months}</div>
            )}
            {chargeAmountCny && <div>≈ ¥{chargeAmountCny}</div>}
          </div>
        </div>
      )}

      <p
        className={`text-xs ${isCard ? 'text-[var(--text-tertiary)]' : 'text-[var(--color-warning)]'}`}
      >
        {isCard
          ? t('settings.netmind.cardAutoRenew',
              'Renews monthly until you cancel. Cancel any time — you keep Pro to the end of the period.')
          : t('settings.netmind.renewNoAutoRenew',
              'One-time purchase — this does NOT auto-renew. Buy again before it ends to stay on Pro.')}
      </p>

      <div className="flex items-center justify-between gap-3">
        <p className="text-[11px] text-[var(--text-tertiary)] flex-1">
          {!isCard && extendsFrom
            ? t('settings.netmind.renewExtendsFrom',
                'Added on top of your current end date, {{date}}.', { date: extendsFrom })
            : ''}
        </p>
        <Button variant="accent" size="sm" onClick={onPay} disabled={busy || total == null}>
          {state === 'processing'
            ? t('settings.netmind.working', 'Working…')
            : isCard
              ? t('settings.netmind.subscribeBtn', 'Subscribe')
              : t('settings.netmind.renewPay', 'Pay ${{total}}', { total: money(total as number) })}
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
