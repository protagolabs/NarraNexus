/**
 * A suspended account's open tab must stop polling: every request would 403,
 * and the edge monitor bans an IP on a stream of 403s (taking down everyone
 * behind that address). The hook signs out, which stops every poll.
 */
import { act, renderHook } from '@testing-library/react';
import { afterEach, beforeEach, expect, test, vi } from 'vitest';
import { useConfigStore } from '@/stores';
import { useAccountSuspendedSignOut } from '../useAccountSuspendedSignOut';
import { wsManager } from '@/services/wsManager';

beforeEach(() => {
  useConfigStore.getState().login('alice', 'jwt');
  vi.spyOn(console, 'warn').mockImplementation(() => {});
});
afterEach(() => {
  useConfigStore.getState().logout();
  vi.restoreAllMocks();
});

test('the account-suspended event signs out, closes WebSockets and shows the notice', async () => {
  const closeAll = vi.spyOn(wsManager, 'closeAll');
  const { result } = renderHook(() => useAccountSuspendedSignOut());
  expect(result.current[0]).toBe(false);
  act(() => { window.dispatchEvent(new CustomEvent('narranexus:account-suspended')); });
  expect(useConfigStore.getState().isLoggedIn).toBe(false);
  await vi.waitFor(() => expect(closeAll).toHaveBeenCalled());
  expect(result.current[0]).toBe(true);
  act(() => { result.current[1](); });
  expect(result.current[0]).toBe(false);
});

test('a later sign-in on the same tab does not inherit the notice', () => {
  const { result } = renderHook(() => useAccountSuspendedSignOut());
  act(() => { window.dispatchEvent(new CustomEvent('narranexus:account-suspended')); });
  expect(result.current[0]).toBe(true);
  act(() => { useConfigStore.getState().login('bob', 'jwt2'); });
  expect(result.current[0]).toBe(false);
});

test('nothing happens when already signed out', () => {
  useConfigStore.getState().logout();
  const { result } = renderHook(() => useAccountSuspendedSignOut());
  act(() => { window.dispatchEvent(new CustomEvent('narranexus:account-suspended')); });
  expect(result.current[0]).toBe(false);
});
