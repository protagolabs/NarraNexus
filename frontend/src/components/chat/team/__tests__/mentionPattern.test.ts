/**
 * @file_name: mentionPattern.test.ts
 * @description: The highlighted names and the woken agents come from one rule.
 *
 * Three places in this folder answer a question about `@`: the plain-text
 * renderer (the user's own messages), the markdown AST renderer (everything an
 * agent says), and the composer's send path. Each had its own hand-copied
 * regex, under comments insisting they must match.
 *
 * The failure that costs the most is not a missing highlight — it is the two
 * halves DISAGREEING. A reader who sees three names lit up and two teammates
 * answer cannot tell which half is wrong, and neither can the person debugging
 * it. So the tokens come from one place.
 *
 * What deliberately does NOT come from one place is what happens next: the
 * renderers ask "is this word a member" strictly (or an email address lights
 * up), and the send path resolves loosely (first names, prefixes — someone
 * typing `@ana` for "Ana Silva" means her). Those are different questions about
 * the same tokens, and the tests below pin the difference rather than erase it.
 */
import { describe, expect, test } from 'vitest';

import { isAddressed, mentionMatcher, mentionTokens } from '../mentionPattern';

describe('mentionTokens', () => {
  test('picks out the @words, lowercased', () => {
    expect(mentionTokens('hey @Bruno and @Ana')).toEqual(new Set(['bruno', 'ana']));
  });

  test('accepts CJK names', () => {
    // The character class is shared with the server's `_extract_team_mentions`;
    // a name this side cannot see is a teammate the user thinks they addressed.
    expect(mentionTokens('@小明 看一下')).toEqual(new Set(['小明']));
  });

  test('an email address is one token, not a mention of its domain', () => {
    // It still TOKENISES — deciding it is not a person is the next question's
    // job, and the two renderers answer it differently from the send path.
    expect(mentionTokens('mail me at bin@example.com')).toEqual(new Set(['example']));
  });

  test('no @ means no tokens', () => {
    expect(mentionTokens('nothing to see')).toEqual(new Set());
  });

  test('a repeated mention counts once', () => {
    expect(mentionTokens('@ana @ana @ana')).toEqual(new Set(['ana']));
  });
});

describe('the matcher is not shared state', () => {
  test('two matchers do not inherit each other lastIndex', () => {
    // A module-level global regex would: `exec` advances `lastIndex`, so the
    // second caller starts mid-string and silently misses the first mention.
    const a = mentionMatcher();
    a.exec('@ana @bruno');
    const b = mentionMatcher();
    expect(b.exec('@ana @bruno')?.[1]).toBe('ana');
  });

  test('calling mentionTokens twice gives the same answer', () => {
    const text = '@ana and @bruno';
    expect(mentionTokens(text)).toEqual(mentionTokens(text));
  });
});

describe('the two questions asked of a token', () => {
  const members = new Set(['ana silva', 'bruno']);

  test('@all and @everyone always address someone', () => {
    expect(isAddressed('all', members)).toBe(true);
    expect(isAddressed('everyone', members)).toBe(true);
  });

  test('the render side is strict — an email domain is not a member', () => {
    expect(isAddressed('example', members)).toBe(false);
  });

  test('the render side does not match on a first name', () => {
    // And this is the DIFFERENCE, stated on purpose: the composer resolves
    // `@ana` to Ana Silva because refusing to wake her is the worse answer,
    // while highlighting a partial match would light up words that address
    // nobody. Same token, two answers, both correct for their side.
    expect(isAddressed('ana', members)).toBe(false);
  });
});
