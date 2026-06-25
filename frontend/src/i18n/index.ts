/**
 * i18n setup (react-i18next).
 *
 * Frontend UI chrome only — agent replies come back in the user's language
 * from the LLM, so they're not translated here.
 *
 * Languages match the narra.nexus homepage set. Adding/removing one is two
 * steps: drop a `locales/<code>.json` (same key shape as `en.json`, the
 * base/fallback) and add an entry to SUPPORTED_LANGUAGES. The language is
 * auto-detected from a prior choice (localStorage) then the browser, falling
 * back to English; the LanguageToggle persists changes and applies dir/lang.
 */
import i18n from 'i18next';
import { initReactI18next } from 'react-i18next';
import LanguageDetector from 'i18next-browser-languagedetector';

import en from './locales/en.json';
import zh from './locales/zh.json';
import ja from './locales/ja.json';
import ko from './locales/ko.json';
import es from './locales/es.json';
import fr from './locales/fr.json';
import de from './locales/de.json';
import ru from './locales/ru.json';
import pt from './locales/pt.json';
import ar from './locales/ar.json';

/**
 * Languages offered in the UI (narra.nexus homepage set). `flag` is an emoji
 * for the switcher; `label` is the endonym (the language's own name).
 */
export const SUPPORTED_LANGUAGES = [
  { code: 'en', label: 'English', flag: '🇺🇸' },
  { code: 'zh', label: '中文', flag: '🇨🇳' },
  { code: 'ja', label: '日本語', flag: '🇯🇵' },
  { code: 'ko', label: '한국어', flag: '🇰🇷' },
  { code: 'es', label: 'Español', flag: '🇪🇸' },
  { code: 'fr', label: 'Français', flag: '🇫🇷' },
  { code: 'de', label: 'Deutsch', flag: '🇩🇪' },
  { code: 'ru', label: 'Русский', flag: '🇷🇺' },
  { code: 'pt', label: 'Português', flag: '🇧🇷' },
  { code: 'ar', label: 'العربية', flag: '🇸🇦' },
] as const;

export type LanguageCode = (typeof SUPPORTED_LANGUAGES)[number]['code'];

/** Right-to-left languages — drive `dir="rtl"` on the document. */
export const RTL_LANGUAGES: readonly LanguageCode[] = ['ar'];

/** localStorage key the detector caches the chosen language under. */
export const LANG_STORAGE_KEY = 'nx_lang';

i18n
  .use(LanguageDetector)
  .use(initReactI18next)
  .init({
    resources: {
      en: { translation: en },
      zh: { translation: zh },
      ja: { translation: ja },
      ko: { translation: ko },
      es: { translation: es },
      fr: { translation: fr },
      de: { translation: de },
      ru: { translation: ru },
      pt: { translation: pt },
      ar: { translation: ar },
    },
    fallbackLng: 'en',
    // English is the base. Anything the detector resolves to (e.g. "zh-CN",
    // "pt-BR") is collapsed to its base ("zh", "pt").
    supportedLngs: SUPPORTED_LANGUAGES.map((l) => l.code),
    nonExplicitSupportedLngs: true,
    load: 'languageOnly',
    interpolation: { escapeValue: false }, // React already escapes
    detection: {
      order: ['localStorage', 'navigator'],
      caches: ['localStorage'],
      lookupLocalStorage: LANG_STORAGE_KEY,
    },
  });

/**
 * Keep <html lang> and dir in sync with the active language. RTL languages
 * (Arabic) flip the whole document to right-to-left. Runs on init + every
 * change.
 */
function applyDocumentLang(lng: string) {
  const base = (lng || 'en').split('-')[0] as LanguageCode;
  const dir = RTL_LANGUAGES.includes(base) ? 'rtl' : 'ltr';
  if (typeof document !== 'undefined') {
    document.documentElement.lang = base;
    document.documentElement.dir = dir;
  }
}
applyDocumentLang(i18n.resolvedLanguage || i18n.language || 'en');
i18n.on('languageChanged', applyDocumentLang);

export default i18n;
