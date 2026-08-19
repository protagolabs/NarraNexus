/**
 * The load-bearing contract: the drawer width is persisted ONLY on the
 * explicit drag release. The stored key doubles as an "existing user"
 * signal for the first-run coach, so a mere mount (or an in-flight drag)
 * writing it would silently burn new users' onboarding.
 */

import { describe, it, expect, beforeEach } from 'vitest';
import { renderHook, act } from '@testing-library/react';
import { usePinnedDrawer } from '../usePinnedDrawer';
import {
  DRAWER_PINNED_KEY,
  DRAWER_WIDTH_KEY,
} from '@/components/layout/drawerLayout';

describe('usePinnedDrawer', () => {
  beforeEach(() => {
    window.localStorage.clear();
  });

  it('mounting writes nothing', () => {
    renderHook(() => usePinnedDrawer());
    expect(window.localStorage.getItem(DRAWER_WIDTH_KEY)).toBeNull();
    expect(window.localStorage.getItem(DRAWER_PINNED_KEY)).toBeNull();
  });

  it('an in-flight drag writes nothing; the release persists', () => {
    const { result } = renderHook(() => usePinnedDrawer());
    act(() => result.current.handleResize(500));
    expect(window.localStorage.getItem(DRAWER_WIDTH_KEY)).toBeNull();
    act(() => result.current.handleResizeEnd(500));
    expect(window.localStorage.getItem(DRAWER_WIDTH_KEY)).not.toBeNull();
  });

  it('pin choices persist and read back', () => {
    const { result } = renderHook(() => usePinnedDrawer());
    expect(result.current.pinned).toBe(true);
    act(() => result.current.setPinned(false));
    expect(window.localStorage.getItem(DRAWER_PINNED_KEY)).toBe('0');
    const { result: second } = renderHook(() => usePinnedDrawer());
    expect(second.current.pinned).toBe(false);
  });
});
