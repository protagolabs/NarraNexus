/**
 * @file_name: PayPage.tsx
 * @author: NarraNexus
 * @date: 2026-07-31
 * @description: /pay — the website-to-Stripe bounce route. No UI beyond a
 * spinner: it exists so a pricing-page CTA can promise "click the plan, land
 * on checkout".
 *
 * Why a dedicated route (P0 bug "付费流程断裂", deadline 2026-08-08): the
 * website cannot create a checkout session itself — sessions are minted by
 * NetMind's billing API and require the user's NetMind loginToken, which only
 * an authenticated app session holds. So "website → Stripe directly" is
 * really "website → smallest possible authenticated hop → Stripe". This page
 * is that hop:
 *
 *   logged in + free      → subscribe() → checkout_url → same-tab redirect
 *   logged out            → ProtectedRoute sends /login?next=%2Fpay, and the
 *                           login/signup flows honor `next` — payment intent
 *                           survives authentication
 *   already subscribed    → /app/settings?tab=account (manage, don't re-buy)
 *   not a Power account   → /app/settings?tab=account (no token to pay with;
 *                           the panel explains the account situation)
 *   subscribe failed      → inline error + retry, plus a link to the account
 *                           page as the manual fallback
 *
 * Same-tab redirect (location.assign), NOT platform.openExternal: the user
 * clicked a plan on the website and this tab's only job is checkout. Stripe's
 * return URL brings them back to /app/settings?tab=account (see
 * backend/routes/billing.py::_return_urls), which closes the loop.
 */

import { useCallback, useEffect, useRef, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { useTranslation } from 'react-i18next';
import { Button } from '@/components/ui';
import { useConfigStore } from '@/stores';
import { api } from '@/lib/api';

type PayPhase = 'working' | 'error';

const ACCOUNT_PAGE = '/app/settings?tab=account';

export function PayPage() {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const isPowerUser = !!useConfigStore((s) => s.netmindToken);
  const [phase, setPhase] = useState<PayPhase>('working');
  const [errorText, setErrorText] = useState('');
  // One checkout session per visit: StrictMode double-fires effects in dev,
  // and a retry re-enters run() manually — the ref keeps concurrent runs out.
  const inFlight = useRef(false);

  const run = useCallback(async () => {
    if (inFlight.current) return;
    inFlight.current = true;
    setPhase('working');
    try {
      // Already subscribed → manage on the account page instead of minting a
      // second checkout session. resolveState semantics (NetmindAccountPanel):
      // any ACTIVE subscription is Pro, auto_renew only splits active/cancelled.
      const sub = await api.getSubscription();
      if (sub.success && sub.data?.subscription?.status === 'ACTIVE') {
        navigate(ACCOUNT_PAGE, { replace: true });
        return;
      }
      const r = await api.subscribe();
      const url = r.data?.checkout_url;
      if (!url) throw new Error(r.error || 'No checkout URL returned');
      // Backend allowlists the host (https://*.stripe.com) before it ever
      // reaches us — see billing.py::_validate_checkout_url.
      window.location.assign(url);
      // Keep the spinner while the browser unloads; no state change needed.
    } catch (e) {
      setErrorText(e instanceof Error ? e.message : String(e));
      setPhase('error');
      inFlight.current = false;
    }
  }, [navigate]);

  useEffect(() => {
    // Pure-local sessions hold no NetMind loginToken, so subscribe() cannot
    // authenticate — the account page explains what this session is instead.
    if (!isPowerUser) {
      navigate(ACCOUNT_PAGE, { replace: true });
      return;
    }
    void run();
  }, [isPowerUser, navigate, run]);

  return (
    <main
      className="min-h-screen flex flex-col items-center justify-center gap-4 bg-[var(--bg-primary)] px-6"
      aria-busy={phase === 'working'}
    >
      {phase === 'working' ? (
        <>
          <div
            className="w-8 h-8 rounded-full border-2 border-[var(--accent-primary)] border-t-transparent animate-spin"
            role="status"
            aria-label={t('pages.pay.working', 'Taking you to checkout…')}
          />
          <p className="text-sm text-[var(--text-secondary)]">
            {t('pages.pay.working', 'Taking you to checkout…')}
          </p>
        </>
      ) : (
        <div className="max-w-sm w-full text-center space-y-4">
          <h1 className="text-base font-semibold text-[var(--text-primary)]">
            {t('pages.pay.errorTitle', "Couldn't start checkout")}
          </h1>
          <p className="text-sm text-[var(--text-secondary)] break-words">{errorText}</p>
          <div className="flex items-center justify-center gap-3">
            <Button variant="accent" size="sm" onClick={() => void run()}>
              {t('pages.pay.retry', 'Try again')}
            </Button>
            <Button variant="outline" size="sm" onClick={() => navigate(ACCOUNT_PAGE)}>
              {t('pages.pay.goAccount', 'Open account page')}
            </Button>
          </div>
        </div>
      )}
    </main>
  );
}

export default PayPage;
