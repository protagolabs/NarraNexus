/**
 * @file_name: SetupPage.tsx
 * @author: NexusAgent
 * @date: 2026-04-02
 * @description: First-time provider configuration page
 *
 * Shown after login when no LLM providers are configured yet (local
 * mode). The primary surface is the shared OneKeyOnboard card (paste one
 * API key, everything is wired in one call). The "Advanced setup"
 * disclosure (collapsed by default — Owner-preferred layout) opens to:
 *   - Subscription sign-in (SubscriptionConnect, LOCAL MODE ONLY):
 *     Claude Code / Codex — no API key. P0 2026-08-28: this used to be
 *     buried in ProviderSettings' add modal (Advanced → modal → Sign in
 *     tab), so subscription-only users read the landing as "API key
 *     required"; it is now the first thing the fold reveals. Connecting
 *     does NOT auto-navigate — the footer flips to "Get Started" live
 *     and the user leaves when ready. Cloud never renders it: the
 *     backend 403s OAuth card types for non-staff, and the UI must not
 *     advertise that path (direct /setup URL visits included).
 *   - The full ProviderSettings surface.
 *
 * The footer re-probes on every provider change (onProvidersChanged),
 * not just when the disclosure collapses.
 */

import { useEffect, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { useNavigate } from 'react-router-dom';
import { ArrowRight, ChevronDown, ChevronRight, SkipForward } from 'lucide-react';
import { BetaBadge, Button, ScrollArea } from '@/components/ui';
import { BracketSectionLabel, PaperCard } from '@/components/nm';
import { OneKeyOnboard } from '@/components/settings/OneKeyOnboard';
import { ProviderSettings } from '@/components/settings/ProviderSettings';
import { SubscriptionConnect } from '@/components/settings/SubscriptionConnect';
import { useTheme } from '@/hooks';
import { api } from '@/lib/api';
import { addProviderCard, type ProviderRow } from '@/lib/providersApi';
import { captureProductEvent } from '@/lib/productAnalytics';
import { useRuntimeStore } from '@/stores';

export function SetupPage() {
  const navigate = useNavigate();
  const { t } = useTranslation();
  const { isDark } = useTheme();
  const mode = useRuntimeStore((s) => s.mode);
  const [showAdvanced, setShowAdvanced] = useState(false);
  const [providerCount, setProviderCount] = useState(0);
  // Provider record state feeding SubscriptionConnect ("Added ✓" vs the
  // Add-as-Provider button). Same probe as providerCount.
  const [providers, setProviders] = useState<Record<string, ProviderRow>>({});
  const [subError, setSubError] = useState('');
  // Bumped after every successful add through the subscription card, so
  // ProviderSettings (which owns its own provider list) refetches too —
  // otherwise "Your providers" showed stale data until remount.
  const [providersVersion, setProvidersVersion] = useState(0);

  // Funnel: user reached the setup page. React StrictMode double-invokes
  // effects in dev, so a ref guard ensures setup_entered fires exactly once
  // per mount. Fire-and-forget.
  const enteredFired = useRef(false);
  useEffect(() => {
    if (enteredFired.current) return;
    enteredFired.current = true;
    captureProductEvent('setup_entered');
  }, []);

  // Check current provider count on mount and after changes. Routed
  // through api.getProviders so identity travels in the X-User-Id /
  // JWT header — bare fetch used to send neither, and the backend
  // happily fell back to "first user in users table".
  const probe = async () => {
    try {
      const data = await api.getProviders();
      if (data.success && data.data?.providers) {
        setProviderCount(Object.keys(data.data.providers).length);
        setProviders(data.data.providers as Record<string, ProviderRow>);
      }
    } catch {
      // Backend not ready — keep the skip affordance
    }
  };

  // Thin wrapper over the shared POST contract for the subscription
  // card. On success THIS page refreshes its own state directly (probe:
  // footer + SubscriptionConnect record props) and bumps the token so
  // ProviderSettings refetches its own "Your providers" grid — each
  // component owns its refresh; nothing here relies on which siblings
  // happen to be mounted. onProvidersChanged remains a fallback for
  // adds made through ProviderSettings' own modal.
  const addProvider = async (body: Record<string, unknown>): Promise<boolean> => {
    setSubError('');
    const res = await addProviderCard(body, t);
    if (!res.ok) {
      setSubError(res.error);
      return false;
    }
    await probe();
    setProvidersVersion((v) => v + 1);
    return true;
  };

  // Record-state derivation (which subscription cards exist) lives in
  // SubscriptionConnect — this page only hands over the raw list.
  const providerList = Object.values(providers);

  // react-hooks/set-state-in-effect flags probe's setStates, but every
  // set happens after an await — never synchronously in the effect body.
  // Same pattern and suppression as IMChannelsSection.
  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect
    probe();
  }, []);

  // Funnel: which event fires depends on WHICH button the user pressed, not
  // on provider count — "Skip for now" is a skip; the primary "Get Started"
  // button and one-key onboarding completion are completions. Fire-and-forget.
  const finishSetup = (event: 'setup_completed' | 'setup_skipped') => {
    captureProductEvent(event);
    navigate('/app/chat', { replace: true });
  };

  const toggleAdvanced = () => {
    // A stale subscription error from a previous attempt should not
    // greet the user on re-expand.
    setSubError('');
    setShowAdvanced((v) => {
      // Collapse-time re-probe kept as a belt-and-braces fallback; the
      // live path is ProviderSettings' onProvidersChanged below.
      if (v) probe();
      return !v;
    });
  };

  return (
    <div className="h-dvh-safe w-screen flex flex-col bg-[var(--bg-deep)]">
      {/* Header — original logo preserved */}
      <div className="flex flex-col items-center pt-10 pb-6 animate-fade-in gap-3">
        <div className="flex items-center gap-2">
          <img
            src={isDark ? '/logo-dark-mode.svg' : '/logo-light-mode.svg'}
            alt="NarraNexus"
            className="h-14 w-auto object-contain"
          />
          <BetaBadge />
        </div>
        <BracketSectionLabel>{t('pages.setup.oneKeyLabel')}</BracketSectionLabel>
        <h1
          className="text-2xl font-bold"
          style={{ color: 'var(--nm-ink)', fontFamily: 'var(--font-display)' }}
        >
          {t('pages.setup.welcome')}
        </h1>
      </div>

      <ScrollArea className="flex-1">
        <div className="max-w-2xl mx-auto px-4 pb-8 animate-fade-in" style={{ animationDelay: '0.05s' }}>
          {/* Primary: one-key onboarding (shared with Settings) */}
          <OneKeyOnboard onComplete={() => finishSetup('setup_completed')} />

          {/* Advanced (collapsed by default, Owner-preferred layout):
            * subscription sign-in first, then the full provider
            * configuration surface. Two cloud gates, both needed:
            * mode !== 'cloud-web' here (fast, also hides the heading;
            * negative match so a not-yet-hydrated null mode fails open to
            * the local P0 fix), and SubscriptionConnect self-gates on the
            * status routes' allowed flag (authoritative, covers every
            * caller). Connecting a subscription does NOT auto-navigate:
            * the footer flips to "Get Started" live (via
            * onProvidersChanged → probe) and the user leaves when ready. */}
          <div className="mt-6">
            <button
              type="button"
              className="flex items-center gap-1.5 mx-auto text-sm hover:opacity-80"
              style={{ color: 'var(--nm-ink70)' }}
              onClick={toggleAdvanced}
            >
              {showAdvanced ? (
                <ChevronDown className="w-4 h-4" />
              ) : (
                <ChevronRight className="w-4 h-4" />
              )}
              {t('pages.setup.advancedSetup')}
            </button>
            {showAdvanced && (
              <div className="mt-4 flex flex-col gap-4">
                {mode !== 'cloud-web' && (
                  <PaperCard padding="lg">
                    <div className="flex flex-col gap-4">
                      <div>
                        <h2
                          className="text-lg font-bold"
                          style={{ color: 'var(--nm-ink)', fontFamily: 'var(--font-display)' }}
                        >
                          {t('pages.setup.subscriptionTitle')}
                        </h2>
                        <p className="text-sm mt-1" style={{ color: 'var(--nm-ink70)' }}>
                          {t('pages.setup.subscriptionSubtitle')}
                        </p>
                      </div>
                      {subError && (
                        <p className="text-sm" role="alert" style={{ color: 'var(--color-error)' }}>
                          {subError}
                        </p>
                      )}
                      <SubscriptionConnect
                        providers={providerList}
                        addProvider={addProvider}
                      />
                    </div>
                  </PaperCard>
                )}
                <ProviderSettings onProvidersChanged={probe} refreshToken={providersVersion} />
              </div>
            )}
          </div>
        </div>
      </ScrollArea>

      {/* Footer actions */}
      <div className="flex items-center justify-center gap-4 py-6 border-t border-[var(--border-default)]">
        {providerCount > 0 ? (
          <Button variant="accent" onClick={() => finishSetup('setup_completed')}>
            {t('pages.setup.getStarted')}
            <ArrowRight className="w-4 h-4 ml-1" />
          </Button>
        ) : (
          <Button variant="ghost" onClick={() => finishSetup('setup_skipped')}>
            <SkipForward className="w-4 h-4 mr-1" />
            {t('pages.setup.skipForNow')}
          </Button>
        )}
      </div>
    </div>
  );
}

export default SetupPage;
