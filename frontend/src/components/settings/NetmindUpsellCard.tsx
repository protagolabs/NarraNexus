/**
 * @file NetmindUpsellCard.tsx
 * @author NetMind.AI
 * @date 2026-07-10
 * @description The Pro upsell card, shown only at the decision moment (Free user
 * whose free tier is used up). Leads with the ONLY thing that differentiates Pro
 * from a one-time top-up — member pricing on popular models + the full model
 * library — because credit-for-credit a $19 subscription and a $19 top-up are
 * identical. Price/grant/period come from GET /api/billing/plans (no hardcoding;
 * price is shown from monthly_grant_usd per product decision A). Presentational.
 */

import { useTranslation } from 'react-i18next';
import { Button } from '@/components/ui';
import { money, formatPeriod } from './netmindFormat';
import type { SubscriptionPlan } from '@/types';

interface NetmindUpsellCardProps {
  proPlan: SubscriptionPlan | null;
  onUpgrade: () => void;
  busy: boolean;
}

export function NetmindUpsellCard({ proPlan, onUpgrade, busy }: NetmindUpsellCardProps) {
  const { t } = useTranslation();
  const period = formatPeriod(proPlan?.prices?.[0]?.period, t('settings.netmind.perMonth', 'mo'));
  const priceText =
    proPlan?.monthly_grant_usd != null
      ? t('settings.netmind.grantPerPeriod', '{{amount}} / {{period}}', {
          amount: `$${money(proPlan.monthly_grant_usd)}`,
          period,
        })
      : null;
  // member_price is the whole reason to subscribe; show the perk unless the plan
  // explicitly says otherwise.
  const hasMemberPrice = proPlan?.features?.member_price !== false;

  return (
    <div className="rounded-md border border-[var(--border-default)] p-3.5 space-y-3 bg-[var(--bg-sunken)]">
      <div className="flex items-baseline justify-between gap-3">
        <span className="text-sm font-semibold text-[var(--text-primary)]">
          {t('settings.netmind.upsellName', 'Upgrade to Pro')}
        </span>
        {priceText && (
          <span className="text-xs text-[var(--text-secondary)] tabular-nums">{priceText}</span>
        )}
      </div>

      <ul className="space-y-1.5">
        {hasMemberPrice && (
          <li className="flex gap-2 text-sm text-[var(--text-primary)]">
            <span aria-hidden className="text-[var(--accent-primary)] font-semibold">✦</span>
            <span className="font-medium">
              {t('settings.netmind.upsellPerkMember',
                'Member pricing on popular models — up to 50% off')}
            </span>
          </li>
        )}
        <li className="flex gap-2 text-sm text-[var(--text-primary)]">
          <span aria-hidden className="text-[var(--accent-primary)] font-semibold">✦</span>
          <span>{t('settings.netmind.upsellPerkLibrary', 'Unlock the full 100+ model library')}</span>
        </li>
        {priceText && (
          <li className="flex gap-2 text-xs text-[var(--text-tertiary)]">
            <span aria-hidden className="opacity-40">·</span>
            <span>
              {/* No {{period}} here — the price line above already carries it,
                  and "每月含 … / 月" read as a broken duplicate. */}
              {t('settings.netmind.upsellGrantLine', 'Includes {{amount}} in monthly credits', {
                amount: `$${money(proPlan?.monthly_grant_usd)}`,
              })}
            </span>
          </li>
        )}
      </ul>

      <Button variant="accent" size="sm" onClick={onUpgrade} disabled={busy} className="w-full">
        {busy
          ? t('settings.netmind.working', 'Working…')
          : t('settings.netmind.upsellName', 'Upgrade to Pro')}
      </Button>
    </div>
  );
}
