/**
 * i18n setup (react-i18next).
 *
 * Frontend UI chrome only — agent replies come back in the user's language
 * from the LLM, so they're not translated here.
 *
 * Adding a language is two steps: drop a `locales/<code>.json` (same key shape
 * as `en.json`, the base/fallback) and add an entry to SUPPORTED_LANGUAGES.
 * The language is auto-detected from a prior choice (localStorage) then the
 * browser, falling back to English; the LanguageToggle persists changes.
 */
import i18n from 'i18next';
import { initReactI18next } from 'react-i18next';
import LanguageDetector from 'i18next-browser-languagedetector';

import en from './locales/en.json';
import zh from './locales/zh.json';

/** Languages offered in the UI. Extend this as locale files are added. */
export const SUPPORTED_LANGUAGES = [
  { code: 'en', label: 'English' },
  { code: 'zh', label: '中文' },
] as const;

export type LanguageCode = (typeof SUPPORTED_LANGUAGES)[number]['code'];

/** localStorage key the detector caches the chosen language under. */
export const LANG_STORAGE_KEY = 'nx_lang';

i18n
  .use(LanguageDetector)
  .use(initReactI18next)
  .init({
    resources: {
      en: { translation: en },
      zh: { translation: zh },
    },
    fallbackLng: 'en',
    // English is the base; only `en` + `zh` ship today. Anything the detector
    // resolves to (e.g. "zh-CN") is collapsed to its base ("zh").
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

export default i18n;
