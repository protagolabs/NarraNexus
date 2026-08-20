/**
 * @file_name: drafts.test.ts
 * @description: The draft layer's failure honesty (review #334 r3 I3): a
 * rejected write must INVALIDATE the previous draft — restoring an older
 * text under a "your unsaved changes" banner is worse than losing it.
 */

import { beforeEach, describe, expect, it, vi } from 'vitest';
import { readDraft, writeDraft } from '../drafts';

beforeEach(() => localStorage.clear());

describe('writeDraft failure paths', () => {
  it('an oversize write removes the previous draft', () => {
    expect(writeDraft('a1', { text: 'small', baseHash: 'h' })).toBe(true);
    expect(readDraft('a1')?.text).toBe('small');
    expect(writeDraft('a1', { text: 'x'.repeat(600 * 1024), baseHash: 'h' })).toBe(false);
    expect(readDraft('a1')).toBeNull();
  });

  it('a quota failure removes the previous draft', () => {
    expect(writeDraft('a2', { text: 'small', baseHash: 'h' })).toBe(true);
    // spy on the INSTANCE — test-setup may install a plain in-memory
    // localStorage that is not a Storage prototype instance.
    const original = localStorage.setItem.bind(localStorage);
    const spy = vi
      .spyOn(localStorage, 'setItem')
      .mockImplementation((k: string, v: string) => {
        if (k.includes('a2')) throw new DOMException('quota', 'QuotaExceededError');
        return original(k, v);
      });
    expect(writeDraft('a2', { text: 'medium', baseHash: 'h' })).toBe(false);
    spy.mockRestore();
    expect(readDraft('a2')).toBeNull();
  });
});
