/**
 * @file NetmindAccountPanel.tsx
 * @author NetMind.AI
 * @date 2026-07-02
 * @description NetMind account & subscription panel (Phase 1: status + sandbox notice).
 *
 * Cloud-web only. Reads GET /api/billing/subscription and renders one of four
 * states (S0 hidden in local mode, S1 Free, S2 Pro active, S3 cancelled but
 * still in-period). Also carries the sandbox free-tier notice (module G).
 *
 * Mirrors QuotaPanel: cloud-mode gate + `return null` when not applicable, no
 * layout shift. Copy is fully i18n-keyed (settings.netmind.*) — English source
 * in the inline defaults (binding rule #1), translations in the locale files.
 * Balance/consumption (module B), subscribe/cancel (C/D), recharge (E), and
 * "use this subscription" (F) land in later phases on the same panel.
 */

import { useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { api } from '@/lib/api';
import type { SubscriptionMe } from '@/types';
import { useRuntimeStore } from '@/stores/runtimeStore';

type PanelState = 'loading' | 'error' | 'free' | 'pro_active' | 'pro_cancelled';

function resolveState(me: SubscriptionMe | null): PanelState {
  if (!me) return 'error';
  const sub = me.subscription;
  if (!sub) return 'free'; // S1
  if (sub.status === 'ACTIVE' && sub.auto_renew) return 'pro_active'; // S2
  if (sub.status === 'ACTIVE' && !sub.auto_renew) return 'pro_cancelled'; // S3
  // Any other status (EXPIRED / PAST_DUE / future NetMind states) — Phase 1
  // has no dedicated UI for these; treat as free-tier display. Phase 2 should
  // add explicit states once NetMind documents them.
  return 'free';
}

function formatDate(unixSeconds: number): string {
  try {
    return new Date(unixSeconds * 1000).toISOString().slice(0, 10);
  } catch {
    return '—';
  }
}

export function NetmindAccountPanel() {
  const { t } = useTranslation();
  const mode = useRuntimeStore((s) => s.mode);
  const isCloud = mode === 'cloud-web';
  const [me, setMe] = useState<SubscriptionMe | null>(null);
  const [state, setState] = useState<PanelState>('loading');

  useEffect(() => {
    if (!isCloud) return; // S0 — hidden entirely in local mode
    let cancelled = false;
    api
      .getSubscription()
      .then((r) => {
        if (cancelled) return;
        const data = r.data ?? null;
        setMe(data);
        setState(resolveState(data));
      })
      .catch(() => {
        if (!cancelled) setState('error');
      });
    return () => {
      cancelled = true;
    };
  }, [isCloud]);

  if (!isCloud) return null; // S0

  return (
    <div className="space-y-3">
      <div className="rounded-md border border-[var(--border-default)] bg-[var(--bg-primary)] p-3">
        <div className="flex items-center justify-between mb-2">
          <h4 className="text-sm font-medium text-[var(--text-primary)]">
            {t('settings.netmind.title', 'NetMind Account & Subscription')}
          </h4>
          {(state === 'pro_active' || state === 'pro_cancelled') && (
            <span className="text-xs text-[var(--accent-primary)]">
              {t('settings.netmind.planPro', 'Pro')}
            </span>
          )}
        </div>

        {state === 'loading' && (
          <p className="text-sm text-[var(--text-secondary)]">
            {t('settings.netmind.loading', 'Loading…')}
          </p>
        )}

        {state === 'error' && (
          <p className="text-sm text-[var(--color-error)]">
            {t(
              'settings.netmind.error',
              'Could not load subscription status. If your login expired, sign in again and refresh.',
            )}
          </p>
        )}

        {state === 'free' && (
          <p className="text-sm text-[var(--text-secondary)]">
            {t('settings.netmind.free', 'Current plan: Free (not subscribed)')}
          </p>
        )}

        {state === 'pro_active' && me?.subscription && (
          <div className="text-sm text-[var(--text-secondary)] space-y-1">
            <div>{t('settings.netmind.proActive', 'Current plan: Pro · active')}</div>
            <div className="text-xs text-[var(--text-tertiary)]">
              {t('settings.netmind.validUntil', 'Valid until')}{' '}
              {formatDate(me.subscription.current_period_end)} ·{' '}
              {t('settings.netmind.autoRenewOn', 'auto-renew on')}
            </div>
          </div>
        )}

        {state === 'pro_cancelled' && me?.subscription && (
          <div className="text-sm text-[var(--text-secondary)] space-y-1">
            <div className="text-[var(--color-warning)]">
              {t('settings.netmind.proCancelled', 'Cancelled · still active this period')}
            </div>
            <div className="text-xs text-[var(--text-tertiary)]">
              {t('settings.netmind.expiresDowngrade', 'Valid until {{date}}, then downgrades to Free', {
                date: formatDate(me.subscription.current_period_end),
              })}
            </div>
          </div>
        )}
      </div>

      {/* Module G — sandbox free-tier notice (platform-side copy) */}
      <div className="rounded-md border border-[var(--border-subtle)] bg-[var(--bg-sunken)] p-3 text-xs text-[var(--text-tertiary)] leading-relaxed">
        {t(
          'settings.netmind.sandboxNotice',
          'NarraNexus sandbox service is free for now — you are not charged for sandbox usage. Billing will start later; we will notify you beforehand.',
        )}
      </div>
    </div>
  );
}
