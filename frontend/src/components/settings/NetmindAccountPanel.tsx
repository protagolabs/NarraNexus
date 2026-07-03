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

  // Plan badge (top-right): reflects the NetMind.AI Power plan state.
  const planBadge = (() => {
    if (state === 'pro_active') {
      return (
        <span className="shrink-0 text-[11px] font-medium px-2 py-0.5 rounded-full bg-[var(--accent-primary)]/12 text-[var(--accent-primary)]">
          {t('settings.netmind.planPro', 'Pro')}
        </span>
      );
    }
    if (state === 'pro_cancelled') {
      return (
        <span className="shrink-0 text-[11px] font-medium px-2 py-0.5 rounded-full bg-[var(--color-warning)]/12 text-[var(--color-warning)]">
          {t('settings.netmind.badgeCancelled', 'Pro · ending')}
        </span>
      );
    }
    if (state === 'free') {
      return (
        <span className="shrink-0 text-[11px] font-medium px-2 py-0.5 rounded-full bg-[var(--bg-sunken)] text-[var(--text-tertiary)]">
          {t('settings.netmind.badgeFree', 'Free')}
        </span>
      );
    }
    return null;
  })();

  // Primary action + status line, driven by the current plan state.
  const actionButton = () => {
    if (state === 'free') {
      return (
        <Button variant="accent" size="sm" onClick={handleSubscribe} disabled={busy || polling}>
          {busy ? t('settings.netmind.working', 'Working…') : t('settings.netmind.subscribeBtn', 'Subscribe to Pro')}
        </Button>
      );
    }
    if (state === 'pro_active') {
      return (
        <Button variant="outline" size="sm" onClick={handleCancel} disabled={busy}>
          {busy ? t('settings.netmind.working', 'Working…') : t('settings.netmind.cancelBtn', 'Cancel subscription')}
        </Button>
      );
    }
    if (state === 'pro_cancelled') {
      return (
        <Button variant="accent" size="sm" onClick={handleReactivate} disabled={busy}>
          {busy ? t('settings.netmind.working', 'Working…') : t('settings.netmind.reactivateBtn', 'Resume auto-renew')}
        </Button>
      );
    }
    return null;
  };

  const statusLine = () => {
    if (state === 'free') return t('settings.netmind.free', 'Current plan: Free (not subscribed)');
    if (state === 'pro_active' && me?.subscription) {
      return `${t('settings.netmind.proActive', 'Pro · active')} · ${t('settings.netmind.validUntil', 'valid until')} ${formatDate(me.subscription.current_period_end)}`;
    }
    if (state === 'pro_cancelled' && me?.subscription) {
      return t('settings.netmind.expiresDowngrade', 'Valid until {{date}}, then downgrades to Free', {
        date: formatDate(me.subscription.current_period_end),
      });
    }
    return '';
  };

  return (
    <div className="rounded-lg border border-[var(--border-default)] bg-[var(--bg-primary)] overflow-hidden">
      {/* Header — product brand + plan badge */}
      <div className="flex items-start justify-between gap-3 px-4 py-3 border-b border-[var(--border-subtle)]">
        <div>
          <h3 className="text-sm font-semibold text-[var(--text-primary)]">NetMind.AI Power</h3>
          <p className="text-[11px] text-[var(--text-tertiary)] mt-0.5">
            {t('settings.netmind.subtitle', 'Power plan & credits · used for your LLM API usage')}
          </p>
        </div>
        {planBadge}
      </div>

      {state === 'loading' && (
        <p className="px-4 py-4 text-sm text-[var(--text-secondary)]">
          {t('settings.netmind.loading', 'Loading…')}
        </p>
      )}
      {state === 'error' && (
        <p className="px-4 py-4 text-sm text-[var(--color-error)]">
          {t('settings.netmind.error',
            'Could not load your NetMind.AI Power account. If your login expired, sign in again and refresh.')}
        </p>
      )}

      {state !== 'loading' && state !== 'error' && (
        <div className="px-4 py-4 space-y-4">
          {/* Balance hero */}
          {feeLoaded && fee && (
            <div>
              <div className="text-2xl font-semibold font-mono text-[var(--text-primary)] leading-none">
                ${fee.metrics?.free_credit ?? '—'}
              </div>
              <div className="mt-1 text-xs text-[var(--text-tertiary)]">
                {t('settings.netmind.currentBalance', 'Current balance')}
                {' · '}
                {t('settings.netmind.monthlyGrant', 'Monthly grant')} ${fee.metrics?.monthly_free_credit ?? '—'}
              </div>
              {fee.eligible === false && (
                <div className="mt-1.5 text-xs text-[var(--color-warning)]">
                  {t('settings.netmind.notEligible',
                    'Cannot incur paid usage right now (no balance / not eligible).')}
                </div>
              )}
              {fee.checks?.has_arrears && (
                <div className="mt-1 text-xs text-[var(--color-error)]">
                  {t('settings.netmind.hasArrears', 'You have outstanding arrears.')}
                </div>
              )}
            </div>
          )}

          {/* Plan status + primary action */}
          <div className="flex items-center justify-between gap-3">
            <p className="text-sm text-[var(--text-secondary)]">{statusLine()}</p>
            {actionButton()}
          </div>
          {polling && (
            <p className="text-xs text-[var(--text-tertiary)]">
              {t('settings.netmind.awaitingPayment',
                'Waiting for payment to complete… this panel refreshes automatically. If you already paid, come back to this tab.')}
            </p>
          )}
          {actionError && <p className="text-xs text-[var(--color-error)]">{actionError}</p>}

          {/* Use this NetMind.AI Power account to power the agent */}
          <div className="rounded-md bg-[var(--bg-sunken)] p-3 space-y-2">
            <div className="text-sm font-medium text-[var(--text-primary)]">
              {t('settings.netmind.useTitle', 'Power NarraNexus with this account')}
            </div>
            <p className="text-xs text-[var(--text-tertiary)]">
              {t('settings.netmind.useDesc',
                'Run agent conversations on your NetMind.AI Power credits — no API key to paste.')}
            </p>
            <Button variant="accent" size="sm" onClick={handleUseSubscription} disabled={busy}>
              {busy ? t('settings.netmind.working', 'Working…') : t('settings.netmind.useSubscribeBtn', 'Use this account')}
            </Button>
            {useResult && (
              <p className={`text-xs ${useResult.ok ? 'text-[var(--color-success)]' : 'text-[var(--color-error)]'}`}>
                {useResult.msg}
              </p>
            )}
          </div>

          {/* Recent activity */}
          {records.length > 0 && (
            <div className="pt-3 border-t border-[var(--border-subtle)]">
              <div className="text-xs font-medium text-[var(--text-secondary)] mb-1.5">
                {t('settings.netmind.activityTitle', 'Recent activity')}
              </div>
              <ul className="space-y-1">
                {records.slice(0, 8).map((r) => {
                  const income = r.direction === 'income';
                  return (
                    <li
                      key={r.record_id}
                      className="flex items-center justify-between gap-2 text-xs text-[var(--text-secondary)]"
                    >
                      <span className="text-[var(--text-tertiary)] tabular-nums">
                        {(r.created_at || '').slice(0, 10)}
                      </span>
                      <span className="flex-1 truncate">{r.type || r.kind}</span>
                      <span className={`font-mono ${income ? 'text-[var(--color-success)]' : 'text-[var(--text-primary)]'}`}>
                        {income ? '+' : '−'}${r.amount} {r.currency}
                      </span>
                      <span className="text-[var(--text-tertiary)] w-16 text-right">{r.status}</span>
                    </li>
                  );
                })}
              </ul>
            </div>
          )}
        </div>
      )}

      {/* Muted footer — charging order + scope + sandbox note */}
      <div className="px-4 py-3 border-t border-[var(--border-subtle)] bg-[var(--bg-sunken)] text-[11px] text-[var(--text-tertiary)] leading-relaxed space-y-1.5">
        <div>
          <span className="text-[var(--text-secondary)]">{t('settings.netmind.deductTitle', 'How usage is charged')}: </span>
          {t('settings.netmind.deductOrder',
            'subscription grant first → then account balance (recharge) → paid usage stops when both run out.')}
        </div>
        <div>
          {t('settings.netmind.scopeNote',
            'These NetMind.AI Power credits cover LLM API usage. Compute (GPU) and other pricing are billed separately.')}
        </div>
        <div>
          {t('settings.netmind.sandboxNotice',
            'The NarraNexus sandbox itself is free for now (no sandbox-service charge); billing will start later, with notice.')}
        </div>
      </div>
    </div>
  );
}
