/**
 * @file_name: PrivacySettings.tsx
 * @description: Privacy pane — the two "what leaves this machine" switches.
 *
 * Two consents with deliberately different scopes, and the copy must not
 * blur them:
 *   - Product analytics: per-USER preference, persisted as a DB row
 *     (/api/auth/settings/analytics). Existed in the backend since 06-08
 *     but its only UI lived in the never-mounted SettingsModal — this
 *     pane is what finally makes it reachable.
 *   - Diagnostic telemetry: per-MACHINE marker file
 *     (/api/auth/settings/telemetry). `controllable=false` (deployment
 *     env override, or multi-tenant cloud) renders the toggle disabled
 *     with an explanation instead of hiding it — a switch that silently
 *     vanishes reads as "there is no telemetry", which would be false.
 *
 * Both toggles are optimistic with revert-on-error (the pattern the
 * analytics toggle already used).
 */

import { useCallback, useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { api, type TelemetryConsentState } from '@/lib/api';
import { Toggle } from '@/components/nm/form';

function ConsentRow({
  title,
  desc,
  note,
  checked,
  disabled,
  onChange,
}: {
  title: string;
  desc: string;
  note?: string;
  checked: boolean;
  disabled?: boolean;
  onChange: (next: boolean) => void;
}) {
  return (
    <div
      className="flex items-start justify-between gap-4 py-4 border-b"
      style={{ borderColor: 'var(--nm-line)' }}
    >
      <div className="space-y-1 min-w-0">
        <div className="text-sm font-medium" style={{ color: 'var(--nm-ink)' }}>
          {title}
        </div>
        <p className="text-sm" style={{ color: 'var(--nm-ink70)' }}>
          {desc}
        </p>
        {note && (
          <p className="text-xs" style={{ color: 'var(--nm-ink50)' }}>
            {note}
          </p>
        )}
      </div>
      <Toggle
        checked={checked}
        disabled={disabled}
        onChange={onChange}
        ariaLabel={title}
      />
    </div>
  );
}

export function PrivacySettings() {
  const { t } = useTranslation();

  const [analyticsEnabled, setAnalyticsEnabled] = useState<boolean | null>(null);
  const [analyticsBusy, setAnalyticsBusy] = useState(false);
  const [telemetry, setTelemetry] = useState<TelemetryConsentState | null>(null);
  const [telemetryBusy, setTelemetryBusy] = useState(false);

  useEffect(() => {
    api
      .getAnalyticsOptOut()
      .then((optedOut) => setAnalyticsEnabled(!optedOut))
      .catch(() => setAnalyticsEnabled(true));
    api
      .getTelemetryConsent()
      .then(setTelemetry)
      .catch(() => {
        /* endpoint unreachable — leave the row in its loading state */
      });
  }, []);

  const toggleAnalytics = useCallback(
    async (next: boolean) => {
      if (analyticsEnabled === null || analyticsBusy) return;
      setAnalyticsEnabled(next);
      setAnalyticsBusy(true);
      try {
        await api.setAnalyticsOptOut(!next);
      } catch {
        setAnalyticsEnabled(!next); // revert on failure
      } finally {
        setAnalyticsBusy(false);
      }
    },
    [analyticsEnabled, analyticsBusy],
  );

  const toggleTelemetry = useCallback(
    async (next: boolean) => {
      if (!telemetry || telemetryBusy || !telemetry.controllable) return;
      const prev = telemetry;
      setTelemetry({
        ...telemetry,
        mode: next ? 'full' : 'off',
        source: next ? 'default' : 'optout',
        opted_out: !next,
      });
      setTelemetryBusy(true);
      try {
        await api.setTelemetryOptOut(!next);
      } catch {
        setTelemetry(prev); // revert on failure
      } finally {
        setTelemetryBusy(false);
      }
    },
    [telemetry, telemetryBusy],
  );

  return (
    <div>
      <ConsentRow
        title={t('pages.settings.privacy.analyticsTitle')}
        desc={t('pages.settings.privacy.analyticsDesc')}
        checked={analyticsEnabled ?? true}
        disabled={analyticsEnabled === null || analyticsBusy}
        onChange={toggleAnalytics}
      />
      <ConsentRow
        title={t('pages.settings.privacy.telemetryTitle')}
        desc={t('pages.settings.privacy.telemetryDesc')}
        note={
          telemetry && !telemetry.controllable
            ? t('pages.settings.privacy.telemetryManaged')
            : t('pages.settings.privacy.telemetryTiming')
        }
        checked={telemetry ? telemetry.mode !== 'off' : false}
        disabled={!telemetry || telemetryBusy || !telemetry.controllable}
        onChange={toggleTelemetry}
      />
    </div>
  );
}
