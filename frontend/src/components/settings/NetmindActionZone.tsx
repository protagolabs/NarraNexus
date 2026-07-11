/**
 * @file NetmindActionZone.tsx
 * @author NetMind.AI
 * @date 2026-07-10
 * @description The plan × runway action zone of the Account & Subscription
 * panel: at most ONE promoted spend action, everything else demoted to links
 * or hidden behind a "Manage" disclosure.
 *
 *   pro_cancelled            → Resume auto-renew (plan-level, runway-agnostic)
 *   free × low               → Upgrade-to-Pro upsell card; top-up demoted to a link
 *   pro_active × low         → top-up promoted directly (already Pro — no upsell)
 *   free × healthy           → nothing but "Manage plan & credits ›"
 *   pro_active × healthy     → member-pricing note + "Manage subscription & balance ›"
 *
 * The "used up" copy is only claimed when the free tier is KNOWN exhausted
 * (freeTierExhausted); an unknown/disabled quota state gets neutral "running
 * low" copy instead — never assert a fact we didn't observe.
 *
 * Purely presentational; guarded money handlers stay in NetmindAccountPanel.
 */

import type { ReactNode } from 'react';
import { useTranslation } from 'react-i18next';
import { platform } from '@/lib/platform';
import { Button } from '@/components/ui';
import { NetmindUpsellCard } from './NetmindUpsellCard';
import type { Runway } from './netmindRunway';
import type { SubscriptionPlan } from '@/types';

// Canonical NetMind pricing page — the "learn more" depth (full library,
// benchmarks, plan comparison) that doesn't belong in the panel.
const PRICING_URL = 'https://www.netmind.ai/pricing';

interface NetmindActionZoneProps {
  state: 'free' | 'pro_active' | 'pro_cancelled';
  runway: Runway;
  /** True only when the free tier is KNOWN exhausted (pct === 0). */
  freeTierExhausted: boolean;
  busy: boolean;
  polling: boolean;
  showManage: boolean;
  onToggleManage: () => void;
  proPlan: SubscriptionPlan | null;
  /** Top-up controls element (state + guards owned by the panel). */
  topUp: ReactNode;
  onSubscribe: () => void;
  onCancel: () => void;
  onReactivate: () => void;
}

export function NetmindActionZone({
  state,
  runway,
  freeTierExhausted,
  busy,
  polling,
  showManage,
  onToggleManage,
  proPlan,
  topUp,
  onSubscribe,
  onCancel,
  onReactivate,
}: NetmindActionZoneProps) {
  const { t } = useTranslation();

  const openPricing = () => void platform.openExternal(PRICING_URL);

  const pricingLink = (
    <div className="flex justify-end">
      <button
        type="button"
        onClick={openPricing}
        className="text-xs text-[var(--text-secondary)] hover:text-[var(--text-primary)] underline underline-offset-2"
      >
        {t('settings.netmind.pricingLink', 'See all models & pricing')} ↗
      </button>
    </div>
  );

  const manageLink = (label: string) => (
    <div className="flex justify-end">
      <button
        type="button"
        onClick={onToggleManage}
        className="text-xs font-medium text-[var(--text-secondary)] hover:text-[var(--text-primary)]"
        aria-expanded={showManage}
      >
        {label} ›
      </button>
    </div>
  );

  if (state === 'pro_cancelled') {
    return (
      <div className="space-y-3">
        <div className="flex items-center justify-between gap-3">
          <p className="text-sm text-[var(--text-secondary)]">
            {t('settings.netmind.proMemberActive', 'Member pricing active on popular models')}
          </p>
          <Button variant="accent" size="sm" onClick={onReactivate} disabled={busy}>
            {busy
              ? t('settings.netmind.working', 'Working…')
              : t('settings.netmind.reactivateBtn', 'Resume auto-renew')}
          </Button>
        </div>
        {manageLink(t('settings.netmind.manageBalance', 'Manage balance'))}
        {showManage && topUp}
      </div>
    );
  }

  if (runway === 'low') {
    if (state === 'free') {
      return (
        <div className="space-y-3">
          <p className="text-sm font-medium text-[var(--color-warning)]">
            {freeTierExhausted
              ? t('settings.netmind.exhaustedChoose', 'Free tier used up. To keep going:')
              : t('settings.netmind.lowChoose', "You're low on credits. To keep going:")}
          </p>
          <NetmindUpsellCard proPlan={proPlan} onUpgrade={onSubscribe} busy={busy || polling} />
          <div className="text-xs text-[var(--text-tertiary)]">
            <button
              type="button"
              onClick={onToggleManage}
              aria-expanded={showManage}
              className="text-[var(--text-secondary)] hover:text-[var(--text-primary)] underline underline-offset-2"
            >
              {t('settings.netmind.topupOrLink', 'Just need a one-time top-up? Add credits')} ›
            </button>
          </div>
          {showManage && topUp}
          {pricingLink}
        </div>
      );
    }
    // pro_active × low → top-up is the action (already Pro, no upsell)
    return (
      <div className="space-y-3">
        <p className="text-sm font-medium text-[var(--color-warning)]">
          {t('settings.netmind.needTopup',
            'Your grant and balance are running low. Add credits to keep going:')}
        </p>
        {topUp}
        {pricingLink}
      </div>
    );
  }

  // healthy
  if (state === 'free') {
    return (
      <div className="space-y-3">
        {manageLink(t('settings.netmind.managePlan', 'Manage plan & credits'))}
        {showManage && (
          <div className="space-y-3">
            {/* Same value-prop card as the low state — a bare "Subscribe"
                button next to bare top-up tiers would recreate the original
                two-peer-spend-buttons confusion, just one click deeper. The
                card states WHY Pro differs from a same-priced top-up. */}
            <NetmindUpsellCard proPlan={proPlan} onUpgrade={onSubscribe} busy={busy || polling} />
            {topUp}
            {pricingLink}
          </div>
        )}
      </div>
    );
  }
  // pro_active × healthy
  return (
    <div className="space-y-3">
      <div className="flex items-center gap-1.5 text-sm text-[var(--color-success)]">
        <span aria-hidden>✦</span>
        <span>{t('settings.netmind.proMemberActive', 'Member pricing active on popular models')}</span>
      </div>
      {manageLink(t('settings.netmind.manageSubscription', 'Manage subscription & balance'))}
      {showManage && (
        <div className="space-y-3">
          <div className="flex items-center justify-between gap-3">
            <p className="text-sm text-[var(--text-secondary)]">
              {t('settings.netmind.cancelBtn', 'Cancel subscription')}
            </p>
            <Button variant="outline" size="sm" onClick={onCancel} disabled={busy}>
              {busy
                ? t('settings.netmind.working', 'Working…')
                : t('settings.netmind.cancelBtn', 'Cancel subscription')}
            </Button>
          </div>
          {topUp}
        </div>
      )}
    </div>
  );
}
