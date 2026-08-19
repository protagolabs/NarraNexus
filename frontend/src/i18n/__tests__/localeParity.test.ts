/**
 * i18n parity gate. en.json is the reference key set; every other locale is
 * measured against it, per NAMESPACE:
 *
 * Namespaces this codebase has fully localized must STAY complete — a new
 * key added to en without its nine siblings fails HERE, in the namespace
 * the author touched, instead of surfacing as one English row inside a
 * localized menu.
 *
 * Deliberately NOT a global missing-key ceiling: a hardcoded total turns
 * every en-only key added by an unrelated PR into a red light with the
 * wrong name on it (the count is branch-relative, so the PR that added the
 * key stays green and the next PR to merge goes red). Per-namespace
 * assertions keep the blame with the change that earned it. The historical
 * backlog (~430 en keys per non-en/zh locale, i18next falls back to
 * English) is a separate backfill effort; grow this list as namespaces get
 * completed.
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

// Namespaces every locale must carry in full. Add a namespace here the
// moment it reaches 10/10 — that is what keeps it complete.
const COMPLETE_NAMESPACES = [
  'layout.teamRowMenu',
  'layout.agentRowMenu',
  'layout.createMenu',
  'chat.team.workspace',
  'chat.header',
  'bookmarks.coach',
  'bookmarks.drawer',
  'pages.settings.nav',
  'pages.settings.personalization',
  'pages.manageAgents',
];

// zh is the co-source locale: it must mirror en in FULL, so a new en key
// missing its zh twin fails regardless of namespace.
const FULL_PARITY_LOCALES = ['zh'];

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

    if (FULL_PARITY_LOCALES.includes(locale)) {
      it(`${locale}: mirrors en in full (co-source locale)`, () => {
        expect(missing).toEqual([]);
      });
    }
  }
});
