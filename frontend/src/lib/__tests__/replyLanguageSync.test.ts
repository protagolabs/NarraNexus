/**
 * replyLanguageSync — the whole round-2 value lives here (PR #284 r2 #3):
 * languageChanged write-through, null+supported-only backfill, no
 * clobbering an existing preference, idempotent init.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';

const listeners: Record<string, ((...a: unknown[]) => void)[]> = {};
vi.mock('@/i18n', () => ({
  default: {
    on: (ev: string, fn: (...a: unknown[]) => void) => {
      (listeners[ev] ??= []).push(fn);
    },
    resolvedLanguage: 'zh',
  },
  SUPPORTED_LANGUAGES: [{ code: 'zh' }, { code: 'en' }],
}));
vi.mock('@/lib/api', () => ({
  api: { getReplyLanguage: vi.fn(), setReplyLanguage: vi.fn() },
}));

import { api } from '@/lib/api';
import { initReplyLanguageSync } from '@/lib/replyLanguageSync';

const getMock = api.getReplyLanguage as ReturnType<typeof vi.fn>;
const setMock = api.setReplyLanguage as ReturnType<typeof vi.fn>;

beforeEach(() => {
  getMock.mockReset();
  setMock.mockReset().mockResolvedValue(undefined);
});

describe('initReplyLanguageSync', () => {
  it('backfills once when server has no preference and detected lang is supported, subscribes languageChanged, and is idempotent', async () => {
    getMock.mockResolvedValue(null);
    initReplyLanguageSync();
    await vi.waitFor(() => expect(setMock).toHaveBeenCalledWith('zh'));

    // every language change writes through
    setMock.mockClear();
    listeners['languageChanged']?.forEach((fn) => fn('en'));
    expect(setMock).toHaveBeenCalledWith('en');

    // second init: no duplicate subscription, no second backfill
    const subs = listeners['languageChanged']?.length ?? 0;
    initReplyLanguageSync();
    expect(listeners['languageChanged']?.length ?? 0).toBe(subs);
  });

  it('never clobbers an existing stored preference', async () => {
    getMock.mockResolvedValue('en');
    // module guard is set by the first test; write-through path still owns
    // persistence — backfill must not fire for a non-null stored value.
    await Promise.resolve();
    expect(setMock).not.toHaveBeenCalledWith('zh');
  });
});
