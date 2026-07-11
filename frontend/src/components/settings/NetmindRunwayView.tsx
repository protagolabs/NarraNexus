/**
 * @file NetmindRunwayView.tsx
 * @author NetMind.AI
 * @date 2026-07-10
 * @description "Runway" block for the Account & Subscription panel: one unified
 * view of what the user has to spend — platform free tier + (Pro) monthly grant
 * + balance — plus the charging-order line and the "free tier first" toggle
 * (formerly QuotaPanel's prefer_system). Purely presentational; the panel owns
 * the data and the toggle handler.
 */

import { useTranslation } from 'react-i18next';

interface NetmindRunwayViewProps {
  /** Free-tier % remaining (0–100), or null when there is no free-tier bar. */
  freePct: number | null;
  /** Pre-formatted monthly grant (e.g. "$19.00 / mo"), or null when not Pro. */
  grantText: string | null;
  /** Pre-formatted spendable balance (e.g. "$9.93"). */
  balanceText: string;
  /** prefer_system state, or null to hide the toggle (feature off / no quota). */
  preferSystem: boolean | null;
  /** True when the toggle may not be turned back ON (free tier exhausted). */
  preferLocked: boolean;
  /** True while a toggle request is in flight (disables the switch). */
  preferBusy: boolean;
  onTogglePrefer: () => void;
  /** Pro users get the three-pool flow line; Free users the two-pool one. */
  flowIsPro: boolean;
}

function Row({ label, value, warn }: { label: string; value: string; warn?: boolean }) {
  return (
    <div className="flex items-center justify-between gap-3 text-sm">
      <span className="text-[var(--text-secondary)]">{label}</span>
      <span
        className={`tabular-nums ${warn ? 'text-[var(--color-warning)]' : 'text-[var(--text-primary)]'}`}
      >
        {value}
      </span>
    </div>
  );
}

export function NetmindRunwayView({
  freePct,
  grantText,
  balanceText,
  preferSystem,
  preferLocked,
  preferBusy,
  onTogglePrefer,
  flowIsPro,
}: NetmindRunwayViewProps) {
  const { t } = useTranslation();
  const exhausted = freePct === 0;

  return (
    <div className="space-y-2.5">
      {freePct !== null && (
        <div className="space-y-1.5">
          <Row
            label={t('settings.netmind.freeTierLabel', 'Free tier')}
            value={
              exhausted
                ? t('settings.netmind.freeTierUsedUp', 'Used up')
                : t('settings.netmind.freeTierLeft', '{{pct}}% left', { pct: freePct })
            }
            warn={exhausted}
          />
          <div
            role="progressbar"
            aria-valuenow={freePct}
            aria-valuemin={0}
            aria-valuemax={100}
            aria-label={t('settings.netmind.freeTierLabel', 'Free tier')}
            className="h-1.5 rounded-full bg-[var(--bg-sunken)] overflow-hidden"
          >
            <div
              className="h-full rounded-full"
              style={{
                width: `${freePct}%`,
                backgroundColor: exhausted ? 'var(--color-warning)' : 'var(--accent-primary)',
              }}
            />
          </div>
        </div>
      )}

      {grantText && (
        <Row label={t('settings.netmind.monthlyGrant', 'Monthly grant')} value={grantText} />
      )}
      <Row label={t('settings.netmind.currentBalance', 'Current balance')} value={balanceText} />

      {/* Charging-order line adapts to what's actually on screen: never claim
          "free tier first" when there is no free-tier bar (feature off /
          unknown) — don't describe a pool the user can't see. */}
      <p className="text-xs text-[var(--text-tertiary)]">
        {freePct !== null
          ? flowIsPro
            ? t('settings.netmind.flowPro',
                'Usage draws the free tier first, then your monthly grant, then your balance.')
            : t('settings.netmind.flowFree',
                'Usage draws the free tier first, then your balance.')
          : flowIsPro
            ? t('settings.netmind.flowProNoTier',
                'Usage draws your monthly grant first, then your balance.')
            : t('settings.netmind.flowFreeNoTier', 'Usage draws from your balance.')}
      </p>

      {preferSystem !== null && (
        <div className="flex items-start justify-between gap-3 pt-2.5 border-t border-[var(--border-subtle)]">
          <div className="min-w-0">
            <div className="text-xs font-medium text-[var(--text-secondary)]">
              {t('settings.netmind.preferTitle', 'Free tier first')}
            </div>
            <div className="text-[11px] text-[var(--text-tertiary)] mt-0.5">
              {preferSystem
                ? t('settings.netmind.preferOnShort',
                    'Uses the platform free tier first, then your own credits.')
                : t('settings.netmind.preferOffShort',
                    'Skips the free tier — uses your own credits directly.')}
            </div>
          </div>
          <button
            type="button"
            role="switch"
            aria-checked={preferSystem}
            aria-label={t('settings.netmind.preferTitle', 'Free tier first')}
            disabled={preferBusy || (preferLocked && !preferSystem)}
            onClick={onTogglePrefer}
            className={`relative inline-flex h-5 w-9 shrink-0 items-center rounded-full transition-colors disabled:cursor-not-allowed disabled:opacity-50 ${
              preferSystem
                ? 'bg-[var(--accent-primary)]'
                : 'bg-[var(--bg-primary)] border border-[var(--border-default)]'
            }`}
          >
            <span
              className={`pointer-events-none inline-block h-3.5 w-3.5 rounded-full bg-white shadow-sm transition-transform ${
                preferSystem ? 'translate-x-4' : 'translate-x-0.5'
              }`}
            />
          </button>
        </div>
      )}
    </div>
  );
}
