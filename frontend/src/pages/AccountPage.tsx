/**
 * @file_name: AccountPage.tsx
 * @author:
 * @date: 2026-08-06
 * @description: User settings — account / billing / subscription, reached
 * from the sidebar's bottom account popover (Chat UI v4 iteration: these
 * concerns moved OUT of the app Settings page into a user-scoped surface).
 *
 * Content is the existing NetmindAccountPanel unchanged — one card owning
 * every "what are my credits / how is usage paid" concern (platform free
 * tier, NetMind Power balance, subscription, top-up). It self-gates to
 * null for non-Power sessions, so this page shows a hint instead of a
 * blank pane if reached by URL without a NetMind login.
 *
 * Stripe returns payers to /app/settings?tab=account&status=… ; the
 * Settings page now redirects that here with the query preserved, so the
 * payment-return handling inside the panel keeps working.
 */
import { useTranslation } from 'react-i18next';
import { NetmindAccountPanel } from '@/components/settings/NetmindAccountPanel';
import { ScrollArea } from '@/components/ui';
import { useConfigStore } from '@/stores/configStore';

export default function AccountPage() {
  const { t } = useTranslation();
  const hasPower = !!useConfigStore((s) => s.netmindToken);

  return (
    <div className="h-full flex flex-col">
      <header className="px-6 pt-6 pb-4 shrink-0">
        <h1
          className="text-3xl font-bold tracking-tight"
          style={{ color: 'var(--nm-ink)', fontFamily: 'var(--font-display)' }}
        >
          {t('pages.settings.nav.account')}
        </h1>
      </header>
      <ScrollArea className="flex-1" viewportClassName="p-6">
        <div className="max-w-3xl mx-auto">
          {hasPower ? (
            <NetmindAccountPanel />
          ) : (
            <p className="text-sm" style={{ color: 'var(--nm-ink70)' }}>
              {t('pages.account.powerOnlyHint')}
            </p>
          )}
        </div>
      </ScrollArea>
    </div>
  );
}
