/**
 * Reply-language sync — the missing half of the i18n fix (review #284-2).
 *
 * The UI language is usually DETECTED (localStorage nx_lang / navigator),
 * not clicked, so a toggle-only write-through never covers existing users.
 * This module (1) subscribes to i18n's languageChanged so EVERY path that
 * changes the UI language persists the reply preference, and (2) backfills
 * once at authenticated boot: server null + a supported detected language
 * -> one PUT. Fire-and-forget throughout; sync must never block the UI.
 */
import i18n, { SUPPORTED_LANGUAGES } from '@/i18n';
import { api } from '@/lib/api';

let initialized = false;

function isSupported(code: string | undefined): code is string {
  return !!code && SUPPORTED_LANGUAGES.some((l) => code === l.code || code.startsWith(l.code));
}

export function initReplyLanguageSync(): void {
  if (initialized) return;
  initialized = true;

  i18n.on('languageChanged', (code: string) => {
    void api.setReplyLanguage(code).catch(() => undefined);
  });

  void (async () => {
    try {
      const stored = await api.getReplyLanguage();
      const current = i18n.resolvedLanguage;
      if (stored === null && isSupported(current)) {
        await api.setReplyLanguage(current);
      }
    } catch {
      // Backfill is best-effort; the next language switch writes through.
    }
  })();
}
