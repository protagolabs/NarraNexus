/**
 * Sizing/persistence policy for the bookmark drawer: pinned by default for a
 * fresh profile (only an explicit unpin turns it off), and the pinned width
 * may grow to 60% of the viewport while always leaving the sidebar + a
 * minimum chat column intact.
 */

import { describe, it, expect } from 'vitest';
import {
  MIN_DRAWER_PX,
  clampDrawerWidth,
  markFirstRunSeen,
  maxDrawerPx,
  readInitialDrawerPinned,
  shouldAutoOpenFirstRun,
  DRAWER_OPENED_ONCE_KEY,
  DRAWER_PINNED_KEY,
  DRAWER_WIDTH_KEY,
} from '../drawerLayout';

const storageWith = (value: string | null) => ({
  getItem: (key: string) => (key === DRAWER_PINNED_KEY ? value : null),
});

describe('readInitialDrawerPinned', () => {
  it('defaults to pinned on a fresh profile', () => {
    expect(readInitialDrawerPinned(storageWith(null))).toBe(true);
  });
  it('respects an explicit unpin', () => {
    expect(readInitialDrawerPinned(storageWith('0'))).toBe(false);
  });
  it('stays pinned for the legacy explicit-pin value', () => {
    expect(readInitialDrawerPinned(storageWith('1'))).toBe(true);
  });
});

describe('drawer width bounds', () => {
  it('allows at least half the viewport on a large screen', () => {
    // 1920: 60% = 1152, reserve leaves 1248 → max 1152 ≥ 50%.
    expect(maxDrawerPx(1920)).toBeGreaterThanOrEqual(1920 / 2);
  });
  it('always leaves the sidebar + minimum chat column on mid screens', () => {
    // 1280: 60% = 768 but reserve allows only 608.
    expect(maxDrawerPx(1280)).toBe(1280 - 672);
  });
  it('never collapses below the minimum width', () => {
    expect(maxDrawerPx(800)).toBe(MIN_DRAWER_PX);
    expect(clampDrawerWidth(10_000, 800)).toBe(MIN_DRAWER_PX);
  });
  it('clamps a stored width into the current viewport bounds', () => {
    expect(clampDrawerWidth(2_000, 1920)).toBe(maxDrawerPx(1920));
    expect(clampDrawerWidth(100, 1920)).toBe(MIN_DRAWER_PX);
    expect(clampDrawerWidth(500, 1920)).toBe(500);
  });

  it('a width chosen on a big screen re-clamps when the window shrinks', () => {
    // 2560 allows 1536 (60%); back on a 1280 window the reserve wins.
    expect(clampDrawerWidth(1536, 2560)).toBe(1536);
    expect(clampDrawerWidth(1536, 1280)).toBe(608);
  });
});

describe('first-run auto-open', () => {
  const mem = () => {
    const m = new Map<string, string>();
    return {
      getItem: (k: string) => m.get(k) ?? null,
      setItem: (k: string, v: string) => void m.set(k, v),
    };
  };

  it('fires on a fresh desktop profile, never after being marked seen', () => {
    const storage = mem();
    expect(shouldAutoOpenFirstRun(storage, false)).toBe(true);
    markFirstRunSeen(storage);
    expect(shouldAutoOpenFirstRun(storage, false)).toBe(false);
  });

  it('a phone visit neither opens nor burns the desktop first run', () => {
    const storage = mem();
    expect(shouldAutoOpenFirstRun(storage, true)).toBe(false);
    expect(shouldAutoOpenFirstRun(storage, false)).toBe(true);
  });

  it('any pre-existing drawer state means an existing user — no re-onboarding', () => {
    for (const key of [DRAWER_OPENED_ONCE_KEY, DRAWER_PINNED_KEY, DRAWER_WIDTH_KEY]) {
      const storage = mem();
      storage.setItem(key, key === DRAWER_PINNED_KEY ? '0' : '1');
      expect(shouldAutoOpenFirstRun(storage, false)).toBe(false);
    }
  });
});
