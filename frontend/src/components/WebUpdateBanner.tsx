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
import { RefreshCw, X } from 'lucide-react';
import { useTranslation } from 'react-i18next';

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
    <div
      // Same floating pill as UpdateBanner (clicks outside pass through), one
      // layer above the amber top banners so a suspended / expired notice
      // cannot hide it.
      className="fixed top-3 left-0 right-0 z-[55] flex justify-center pointer-events-none font-[family-name:var(--font-sans)]"
      role="status"
      aria-live="polite"
    >
      <div className="pointer-events-auto flex items-center gap-3 px-4 py-2 rounded-full shadow-lg border border-[var(--nm-line)] bg-[var(--bg-elev)] text-[var(--nm-ink)] text-sm">
        <RefreshCw className="w-4 h-4 text-[var(--accent-primary)]" aria-hidden="true" />
        <span>{t('appBanners.newWebVersion')}</span>
        <button
          type="button"
          onClick={() => window.location.reload()}
          className="px-3 py-1 rounded-full text-xs font-medium bg-[var(--accent-primary)] text-white hover:opacity-90 transition"
        >
          {t('appBanners.reloadNow')}
        </button>
        <button
          type="button"
          onClick={() => setDismissed(true)}
          aria-label={t('appBanners.dismiss')}
          className="p-1 rounded-full text-[var(--nm-ink50)] hover:text-[var(--nm-ink)] hover:bg-[var(--nm-line)]/40 transition"
        >
          <X className="w-3.5 h-3.5" aria-hidden="true" />
        </button>
      </div>
    </div>
  );
}
