/**
 * @file_name: WebUpdateBanner.tsx
 * @date: 2026-11-02
 * @description: Cloud web: offers a reload once the frontend was redeployed under this tab.
 *
 * The web counterpart of UpdateBanner (which follows the desktop updater).
 * A tab keeps running the bundle it loaded; after a deploy it keeps running
 * the old code — including bugs the deploy fixed — until reloaded. This pill
 * appears once `lib/buildWatch` sees a different deployed build. It never
 * reloads on its own: the user may be mid-message, so the reload is theirs
 * to trigger. Dismiss hides it for this tab.
 *
 * Cloud web only (forced-cloud deployments). Desktop and local runs serve
 * the frontend they were built with and have no deployment to drift from.
 */

import { useEffect, useState } from 'react';
import { RefreshCw } from 'lucide-react';
import { useTranslation } from 'react-i18next';

import { FloatingPill } from '@/components/FloatingPill';
import { startBuildWatch } from '@/lib/buildWatch';
import { isForcedCloud } from '@/lib/runtimeConfig';
import { isTauri } from '@/lib/tauri';

export default function WebUpdateBanner() {
  const { t } = useTranslation();
  const [available, setAvailable] = useState(false);
  const [dismissed, setDismissed] = useState(false);

  useEffect(() => {
    // App mounts this only on forced-cloud web; the check here keeps the
    // component correct wherever it is mounted.
    if (!isForcedCloud() || isTauri()) return;
    return startBuildWatch(() => setAvailable(true));
  }, []);

  if (!available || dismissed) return null;

  return (
    <FloatingPill
      icon={RefreshCw}
      actionLabel={t('appBanners.reloadNow')}
      onAction={() => window.location.reload()}
      onDismiss={() => setDismissed(true)}
      dismissLabel={t('appBanners.dismiss')}
    >
      {t('appBanners.newWebVersion')}
    </FloatingPill>
  );
}
