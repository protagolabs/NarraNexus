---
code_file: frontend/src/i18n/index.ts
last_verified: 2026-06-25
stub: false
---

# i18n/index.ts — react-i18next bootstrap + glob locale loader

Initialises the single app-wide `i18next` instance (imported once for its
side effect from `main.tsx`, before render). Scope is **UI chrome only** —
agent replies come from the LLM in the user's own language and are never
routed through `t()`.

Key design decisions:

- **Glob-loaded resources.** Locales are assembled with
  `import.meta.glob(['./locales/*.json','./locales/*/*.json'], {eager:true})`
  + a small `deepMerge`. Adding/extending a language is therefore just
  dropping a `locales/<lang>/<area>.json` fragment — no edit here. This is
  what let the migration fan out across many agents writing disjoint area
  files with zero merge conflicts. **Gotcha:** Vite's glob set is captured at
  dev-server start; adding a *new* locale file while the server runs needs a
  **fresh** restart (not just HMR) or the new keys render as raw keys.
- **`SUPPORTED_LANGUAGES`** is the homepage's 10-language set (en/zh/ja/ko/
  es/fr/de/ru/pt/ar) with `code`/`label`/`flag`; [[LanguageToggle]] renders
  straight off it.
- **RTL.** `RTL_LANGUAGES = ['ar']`; `applyDocumentLang()` sets
  `<html lang>` + `dir` on init and on every `languageChanged`.
- Persisted under `LANG_STORAGE_KEY` ('nx_lang') via the browser language
  detector. Tests import this module (see `test-setup.ts`) so specs resolve
  real strings.

Plural-category counts differ per language (ar 6, ru 4, CJK 1, en 2) so leaf
key *counts* differ across locales, but the base-key sets are identical.
