/**
 * @file NetmindAccountPanel.tsx
 * @author NetMind.AI
 * @date 2026-07-02
 * @description NetMind account & subscription panel.
 *
 * Cloud-web only. Reads GET /api/billing/subscription and renders one of four
 * states (S0 hidden in local mode, S1 Free, S2 Pro active, S3 cancelled but
 * still in-period), plus the sandbox free-tier notice (module G).
 *
 * Phase 3 adds subscription actions (module C/D): subscribe → Stripe checkout →
 * poll /me until ACTIVE; cancel (confirm) → auto-renew off; reactivate (resume
 * auto-renew). Payment回流 has no deterministic desktop signal, so we also
 * refresh on window focus + poll with a bounded window (C3 mitigation).
 *
 * Mirrors QuotaPanel: cloud-mode gate + `return null` when not applicable.
 * Copy is fully i18n-keyed (settings.netmind.*) with English source defaults.
 * Balance/consumption (module B) and recharge (E) land in later phases.
 */

import { useCallback, useEffect, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { api } from '@/lib/api';
import { platform } from '@/lib/platform';
import { Button } from '@/components/ui';
import type { FeeInfo, FinanceRecord, SubscriptionMe } from '@/types';
import { useRuntimeStore } from '@/stores/runtimeStore';

type PanelState = 'loading' | 'error' | 'free' | 'pro_active' | 'pro_cancelled';

const POLL_INTERVAL_MS = 4000;
const POLL_MAX_MS = 180000; // 3 min bound — never poll forever

function resolveState(me: SubscriptionMe | null): PanelState {
  if (!me) return 'error';
  const sub = me.subscription;
  if (!sub) return 'free'; // S1
  if (sub.status === 'ACTIVE' && sub.auto_renew) return 'pro_active'; // S2
  if (sub.status === 'ACTIVE' && !sub.auto_renew) return 'pro_cancelled'; // S3
  return 'free';
}

function formatDate(unixSeconds: number): string {
  try {
    return new Date(unixSeconds * 1000).toISOString().slice(0, 10);
  } catch {
    return '—';
  }
}

function errMessage(e: unknown): string {
  return e instanceof Error ? e.message : String(e);
}

export function NetmindAccountPanel() {
  const { t } = useTranslation();
  const mode = useRuntimeStore((s) => s.mode);
  const isCloud = mode === 'cloud-web';
  const [me, setMe] = useState<SubscriptionMe | null>(null);
  const [state, setState] = useState<PanelState>('loading');
  const [busy, setBusy] = useState(false); // an action is in flight
  const [polling, setPolling] = useState(false); // awaiting payment回流
  const [actionError, setActionError] = useState<string | null>(null);
  const [fee, setFee] = useState<FeeInfo | null>(null);
  const [feeLoaded, setFeeLoaded] = useState(false);
  const [records, setRecords] = useState<FinanceRecord[]>([]);
  const [useResult, setUseResult] = useState<{ ok: boolean; msg: string } | null>(null);
  const mounted = useRef(true);
  // Synchronous locks: React state (busy/polling) updates are async/batched, so
  // a fast double-click can fire a handler twice before `disabled` re-renders.
  // Refs flip synchronously and are the real guard against duplicate
  // subscribe → duplicate Stripe checkout sessions.
  const busyRef = useRef(false);
  const pollingRef = useRef(false);

  const load = useCallback(async () => {
    // Fetch subscription + balance concurrently; each result is handled
    // independently so a fee-info failure never blanks the subscription status
    // (and vice versa). Note: only a FETCH failure is isolated here — the
    // balance render itself must stay null-safe against a partial 200 payload.
    const [subR, feeR, recR] = await Promise.allSettled([
      api.getSubscription(),
      api.getFeeInfo(),
      api.getRecords(),
    ]);
    if (!mounted.current) return;
    if (subR.status === 'fulfilled') {
      const data = subR.value.data ?? null;
      setMe(data);
      setState(resolveState(data));
    } else {
      setState('error');
    }
    setFee(feeR.status === 'fulfilled' ? feeR.value.data ?? null : null);
    setFeeLoaded(true);
    setRecords(recR.status === 'fulfilled' ? recR.value.data ?? [] : []);
  }, []);

  useEffect(() => {
    mounted.current = true;
    if (isCloud) void load();
    return () => {
      mounted.current = false;
    };
  }, [isCloud, load]);

  // C3 mitigation: no deterministic signal when the user returns from the
  // external Stripe window (esp. desktop). Refresh whenever the tab regains
  // focus so a completed payment reflects without a manual reload.
  useEffect(() => {
    if (!isCloud) return;
    const onFocus = () => void load();
    window.addEventListener('focus', onFocus);
    return () => window.removeEventListener('focus', onFocus);
  }, [isCloud, load]);

  // Poll /me until the subscription flips to ACTIVE (bounded), used after
  // subscribe kicks off an external payment.
  const pollUntilActive = useCallback(async () => {
    if (pollingRef.current) return; // never run two overlapping poll loops
    pollingRef.current = true;
    setPolling(true);
    const deadline = Date.now() + POLL_MAX_MS;
    let becameActive = false;
    try {
      while (Date.now() < deadline) {
        await new Promise((r) => setTimeout(r, POLL_INTERVAL_MS));
        if (!mounted.current) return;
        try {
          const r = await api.getSubscription();
          const data = r.data ?? null;
          if (data?.subscription?.status === 'ACTIVE') {
            setMe(data);
            setState(resolveState(data));
            becameActive = true;
            return;
          }
        } catch {
          /* transient — keep polling until the deadline */
        }
      }
      // Deadline hit without seeing ACTIVE — don't vanish silently.
      if (mounted.current && !becameActive) {
        setActionError(
          t('settings.netmind.pollTimeout',
            "Still not active. If you completed payment, refresh in a moment."),
        );
      }
    } finally {
      pollingRef.current = false;
      if (mounted.current) setPolling(false);
    }
  }, [t]);

  const handleSubscribe = useCallback(async () => {
    if (busyRef.current) return; // synchronous double-click guard
    busyRef.current = true;
    setBusy(true);
    setActionError(null);
    try {
      const r = await api.subscribe();
      const url = r.data?.checkout_url;
      if (!url) throw new Error('No checkout URL returned');
      await platform.openExternal(url);
      void pollUntilActive(); // reflect the result when payment completes
    } catch (e) {
      if (mounted.current) setActionError(errMessage(e));
    } finally {
      busyRef.current = false;
      if (mounted.current) setBusy(false);
    }
  }, [pollUntilActive]);

  const handleCancel = useCallback(async () => {
    if (busyRef.current) return;
    if (!window.confirm(t('settings.netmind.cancelConfirm',
      'Cancel = turn off auto-renew. You stay on Pro until the period ends — no immediate downgrade, no prorated refund. Continue?'))) {
      return;
    }
    busyRef.current = true;
    setBusy(true);
    setActionError(null);
    try {
      await api.cancelSubscription();
      await load();
    } catch (e) {
      if (mounted.current) setActionError(errMessage(e));
    } finally {
      busyRef.current = false;
      if (mounted.current) setBusy(false);
    }
  }, [t, load]);

  const handleReactivate = useCallback(async () => {
    if (busyRef.current) return;
    // reactivate re-enables auto-renew (may trigger a charge) — confirm, since
    // its exact billing semantics are still pending NetMind confirmation.
    if (!window.confirm(t('settings.netmind.reactivateConfirm',
      'Resume auto-renew for your Pro subscription?'))) {
      return;
    }
    busyRef.current = true;
    setBusy(true);
    setActionError(null);
    try {
      await api.reactivateSubscription();
      await load();
    } catch (e) {
      if (mounted.current) setActionError(errMessage(e));
    } finally {
      busyRef.current = false;
      if (mounted.current) setBusy(false);
    }
  }, [t, load]);

  const handleUseSubscription = useCallback(async () => {
    if (busyRef.current) return;
    busyRef.current = true;
    setBusy(true);
    setUseResult(null);
    try {
      await api.useSubscription();
      if (mounted.current) {
        setUseResult({
          ok: true,
          msg: t('settings.netmind.useSubscribeOk',
            'Connected — you can now pick a model in LLM Providers.'),
        });
      }
    } catch (e) {
      if (mounted.current) setUseResult({ ok: false, msg: errMessage(e) });
    } finally {
      busyRef.current = false;
      if (mounted.current) setBusy(false);
    }
  }, [t]);

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
            {t('settings.netmind.error',
              'Could not load subscription status. If your login expired, sign in again and refresh.')}
          </p>
        )}

        {state === 'free' && (
          <div className="space-y-3">
            <p className="text-sm text-[var(--text-secondary)]">
              {t('settings.netmind.free', 'Current plan: Free (not subscribed)')}
            </p>
            <Button variant="accent" size="sm" onClick={handleSubscribe} disabled={busy || polling}>
              {busy
                ? t('settings.netmind.working', 'Working…')
                : t('settings.netmind.subscribeBtn', 'Subscribe to Pro')}
            </Button>
          </div>
        )}

        {state === 'pro_active' && me?.subscription && (
          <div className="text-sm text-[var(--text-secondary)] space-y-2">
            <div>{t('settings.netmind.proActive', 'Current plan: Pro · active')}</div>
            <div className="text-xs text-[var(--text-tertiary)]">
              {t('settings.netmind.validUntil', 'Valid until')}{' '}
              {formatDate(me.subscription.current_period_end)} ·{' '}
              {t('settings.netmind.autoRenewOn', 'auto-renew on')}
            </div>
            <Button variant="outline" size="sm" onClick={handleCancel} disabled={busy}>
              {busy
                ? t('settings.netmind.working', 'Working…')
                : t('settings.netmind.cancelBtn', 'Cancel subscription')}
            </Button>
          </div>
        )}

        {state === 'pro_cancelled' && me?.subscription && (
          <div className="text-sm text-[var(--text-secondary)] space-y-2">
            <div className="text-[var(--color-warning)]">
              {t('settings.netmind.proCancelled', 'Cancelled · still active this period')}
            </div>
            <div className="text-xs text-[var(--text-tertiary)]">
              {t('settings.netmind.expiresDowngrade',
                'Valid until {{date}}, then downgrades to Free',
                { date: formatDate(me.subscription.current_period_end) })}
            </div>
            <Button variant="accent" size="sm" onClick={handleReactivate} disabled={busy}>
              {busy
                ? t('settings.netmind.working', 'Working…')
                : t('settings.netmind.reactivateBtn', 'Resume auto-renew')}
            </Button>
          </div>
        )}

        {polling && (
          <p className="mt-2 text-xs text-[var(--text-tertiary)]">
            {t('settings.netmind.awaitingPayment',
              'Waiting for payment to complete… this panel refreshes automatically. If you already paid, come back to this tab.')}
          </p>
        )}
        {actionError && (
          <p className="mt-2 text-xs text-[var(--color-error)]">{actionError}</p>
        )}
      </div>

      {/* Module F — use this subscription (wire a NetMind key to the agent) */}
      <div className="rounded-md border border-[var(--border-default)] bg-[var(--bg-primary)] p-3 text-sm space-y-2">
        <h4 className="font-medium text-[var(--text-primary)]">
          {t('settings.netmind.useTitle', 'Use this account for NarraNexus')}
        </h4>
        <p className="text-xs text-[var(--text-tertiary)]">
          {t('settings.netmind.useDesc',
            'Run agent conversations on your NetMind balance / subscription — no API key to paste.')}
        </p>
        <Button variant="accent" size="sm" onClick={handleUseSubscription} disabled={busy}>
          {busy
            ? t('settings.netmind.working', 'Working…')
            : t('settings.netmind.useSubscribeBtn', 'Use this subscription')}
        </Button>
        {useResult && (
          <p className={`text-xs ${useResult.ok ? 'text-[var(--color-success)]' : 'text-[var(--color-error)]'}`}>
            {useResult.msg}
          </p>
        )}
      </div>

      {/* Module B — balance + deduction order (degraded view per G1) */}
      {feeLoaded && fee && (
        <div className="rounded-md border border-[var(--border-default)] bg-[var(--bg-primary)] p-3 text-sm space-y-2">
          <h4 className="font-medium text-[var(--text-primary)]">
            {t('settings.netmind.balanceTitle', 'NetMind account balance')}
          </h4>
          <div className="flex justify-between text-[var(--text-secondary)]">
            <span>{t('settings.netmind.currentBalance', 'Current balance')}</span>
            <span className="font-mono">${fee.metrics?.free_credit ?? '—'}</span>
          </div>
          <div className="flex justify-between text-[var(--text-secondary)]">
            <span>{t('settings.netmind.monthlyGrant', 'Monthly grant')}</span>
            <span className="font-mono">${fee.metrics?.monthly_free_credit ?? '—'}</span>
          </div>
          {fee.eligible === false && (
            <div className="text-xs text-[var(--color-warning)]">
              {t('settings.netmind.notEligible',
                'Cannot incur paid usage right now (no balance / not eligible).')}
            </div>
          )}
          {fee.checks?.has_arrears && (
            <div className="text-xs text-[var(--color-error)]">
              {t('settings.netmind.hasArrears', 'You have outstanding arrears.')}
            </div>
          )}

          {/* Deduction order — hard requirement copy (module B) */}
          <div className="mt-2 pt-2 border-t border-[var(--border-subtle)] text-xs text-[var(--text-tertiary)] space-y-0.5">
            <div className="font-medium text-[var(--text-secondary)]">
              {t('settings.netmind.deductTitle', 'How usage is charged')}
            </div>
            <div>{t('settings.netmind.deduct1', '1) Subscription grant is used first')}</div>
            <div>{t('settings.netmind.deduct2', '2) Then your account balance (recharge / top-ups)')}</div>
            <div>{t('settings.netmind.deduct3', '3) When both run out, paid usage stops')}</div>
          </div>

          <div className="text-[11px] text-[var(--text-tertiary)]">
            {t('settings.netmind.balanceDegraded',
              'Per-period usage and the subscription-vs-balance breakdown are not available from NetMind yet.')}
          </div>
        </div>
      )}

      {/* Module B — recent financial activity (consumption + recharge), G1 */}
      {records.length > 0 && (
        <div className="rounded-md border border-[var(--border-default)] bg-[var(--bg-primary)] p-3 text-sm">
          <h4 className="font-medium text-[var(--text-primary)] mb-2">
            {t('settings.netmind.activityTitle', 'Recent activity')}
          </h4>
          <ul className="space-y-1">
            {records.slice(0, 10).map((r) => {
              const income = r.direction === 'income';
              return (
                <li
                  key={r.record_id}
                  className="flex items-center justify-between text-xs text-[var(--text-secondary)]"
                >
                  <span className="text-[var(--text-tertiary)]">
                    {(r.created_at || '').slice(0, 10)}
                  </span>
                  <span className="flex-1 px-2 truncate">{r.type || r.kind}</span>
                  <span
                    className={`font-mono ${income ? 'text-[var(--color-success)]' : 'text-[var(--text-primary)]'}`}
                  >
                    {income ? '+' : '−'}${r.amount} {r.currency}
                  </span>
                  <span className="ml-2 text-[var(--text-tertiary)] w-16 text-right">
                    {r.status}
                  </span>
                </li>
              );
            })}
          </ul>
        </div>
      )}

      {/* Module G — sandbox free-tier notice (platform-side copy) */}
      <div className="rounded-md border border-[var(--border-subtle)] bg-[var(--bg-sunken)] p-3 text-xs text-[var(--text-tertiary)] leading-relaxed">
        {t('settings.netmind.sandboxNotice',
          'NarraNexus sandbox service is free for now — you are not charged for sandbox usage. Billing will start later; we will notify you beforehand.')}
      </div>
    </div>
  );
}
