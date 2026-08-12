/**
 * @file_name: chunkReload.test.ts
 * @description: The one-shot reload guard for stale-chunk (vite:preloadError)
 * recovery. After a deploy, an open tab's lazy import 404s; a single reload
 * fetches the new manifest, but a genuinely broken build must not wedge the tab
 * in a reload loop — so the reload fires at most once per session.
 */
import { describe, expect, test, vi } from 'vitest';
import { handlePreloadError } from '../chunkReload';

function memStorage(): Storage {
  const m = new Map<string, string>();
  return {
    getItem: (k) => m.get(k) ?? null,
    setItem: (k, v) => void m.set(k, v),
    removeItem: (k) => void m.delete(k),
    clear: () => m.clear(),
    key: () => null,
    length: 0,
  } as Storage;
}

describe('handlePreloadError', () => {
  test('reloads once on the first stale-chunk error', () => {
    const reload = vi.fn();
    handlePreloadError(reload, memStorage());
    expect(reload).toHaveBeenCalledTimes(1);
  });

  test('does not reload again in the same session (no reload loop)', () => {
    const reload = vi.fn();
    const storage = memStorage();
    handlePreloadError(reload, storage);
    handlePreloadError(reload, storage);
    handlePreloadError(reload, storage);
    expect(reload).toHaveBeenCalledTimes(1);
  });
});
