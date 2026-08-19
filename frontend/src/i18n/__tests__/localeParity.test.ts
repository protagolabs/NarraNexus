/**
 * i18n parity ratchet. en.json is the reference key set; every other locale
 * is measured against it. Two rules:
 *
 * 1. Namespaces this codebase has fully localized must STAY complete — a
 *    new key added to en without its nine siblings fails here, next to the
 *    edit that introduced it, instead of surfacing as one English row in a
 *    localized menu.
 * 2. The historical backlog (locales missing en keys wholesale) may only
 *    shrink. The counts below are a ceiling, not a target: lower them when
 *    keys are backfilled; the test refuses to let them grow.
 */

import { describe, it, expect } from 'vitest';

import en from '../locales/en.json';
import zh from '../locales/zh.json';
import ar from '../locales/ar.json';
import de from '../locales/de.json';
import es from '../locales/es.json';
import fr from '../locales/fr.json';
import ja from '../locales/ja.json';
import ko from '../locales/ko.json';
import pt from '../locales/pt.json';
import ru from '../locales/ru.json';

const LOCALES: Record<string, unknown> = { zh, ar, de, es, fr, ja, ko, pt, ru };

// Ceiling per locale, measured 2026-08-19. Shrink freely; never raise.
const MISSING_CEILING: Record<string, number> = {
  zh: 0,
  ar: 429,
  de: 429,
  es: 429,
  fr: 429,
  ja: 430,
  ko: 430,
  pt: 429,
  ru: 429,
};

// Namespaces every locale must carry in full.
const COMPLETE_NAMESPACES = [
  'layout.teamRowMenu',
  'layout.agentRowMenu',
  'chat.team.workspace',
  'bookmarks.coach',
  'pages.settings.nav',
  'pages.settings.personalization',
  'pages.manageAgents',
  'chat.header',
  'bookmarks.drawer',
];

function leaves(obj: unknown, prefix = ''): string[] {
  if (typeof obj !== 'object' || obj === null) return [prefix];
  return Object.entries(obj as Record<string, unknown>).flatMap(([k, v]) =>
    leaves(v, prefix ? `${prefix}.${k}` : k),
  );
}

const enLeaves = new Set(leaves(en));

describe('locale parity with en', () => {
  for (const [locale, json] of Object.entries(LOCALES)) {
    const have = new Set(leaves(json));
    const missing = [...enLeaves].filter((k) => !have.has(k));

    it(`${locale}: fully-localized namespaces stay complete`, () => {
      const holes = missing.filter((k) =>
        COMPLETE_NAMESPACES.some((ns) => k.startsWith(`${ns}.`)),
      );
      expect(holes).toEqual([]);
    });

    it(`${locale}: the missing-key backlog never grows (ceiling ${MISSING_CEILING[locale]})`, () => {
      expect(missing.length).toBeLessThanOrEqual(MISSING_CEILING[locale]);
    });
  }
});
