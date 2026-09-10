/**
 * @file_name: useCircuitBannerAutoClear.test.ts
 * @description: The "paused" circuit banner must re-check reality and close
 * itself once the breaker is no longer paused — and must not poll while
 * logged out. Fake timers; the status API is mocked, the decision is not.
 */
import { describe, expect, test, vi, beforeEach, afterEach } from 'vitest';
import { renderHook } from '@testing-library/react';

import { CIRCUIT_BREAKER_POLL_INTERVAL_MS } from '@/services/wsCircuitOpen';

const { getAgentCircuitBreaker } = vi.hoisted(() => ({
  getAgentCircuitBreaker: vi.fn(async () => ({ success: true, cb_status: 'paused' })),
}));

vi.mock('@/lib/api', () => ({ api: { getAgentCircuitBreaker } }));

import { useCircuitBannerAutoClear } from '../useCircuitBannerAutoClear';

const banner = { agentId: 'ag_1', reason: 'paused:auth' };

beforeEach(() => {
  getAgentCircuitBreaker.mockReset();
  getAgentCircuitBreaker.mockResolvedValue({ success: true, cb_status: 'paused' });
  vi.useFakeTimers();
});
afterEach(() => {
  vi.useRealTimers();
});

describe('useCircuitBannerAutoClear', () => {
  test('checks immediately, keeps the banner while still paused, closes once active', async () => {
    const setCircuitOpen = vi.fn();
    renderHook(() => useCircuitBannerAutoClear(banner, setCircuitOpen, true));

    await vi.advanceTimersByTimeAsync(0);
    expect(getAgentCircuitBreaker).toHaveBeenCalledTimes(1);
    expect(getAgentCircuitBreaker).toHaveBeenCalledWith('ag_1');
    expect(setCircuitOpen).not.toHaveBeenCalled();

    getAgentCircuitBreaker.mockResolvedValue({ success: true, cb_status: 'active' });
    await vi.advanceTimersByTimeAsync(CIRCUIT_BREAKER_POLL_INTERVAL_MS);
    expect(getAgentCircuitBreaker).toHaveBeenCalledTimes(2);
    expect(setCircuitOpen).toHaveBeenCalledWith(null);
  });

  test('a fresh banner object for the same agent+reason does not restart the interval', async () => {
    const setCircuitOpen = vi.fn();
    const { rerender } = renderHook(
      ({ b }) => useCircuitBannerAutoClear(b, setCircuitOpen, true),
      { initialProps: { b: { ...banner } } },
    );
    await vi.advanceTimersByTimeAsync(0);
    expect(getAgentCircuitBreaker).toHaveBeenCalledTimes(1);

    // The user retries every few seconds → new detail objects, same content.
    for (let i = 0; i < 5; i++) {
      await vi.advanceTimersByTimeAsync(CIRCUIT_BREAKER_POLL_INTERVAL_MS / 2 - 1);
      rerender({ b: { ...banner } });
    }
    // Only the immediate check ran, plus the ticks 30s apart — never reset.
    const elapsed = 5 * (CIRCUIT_BREAKER_POLL_INTERVAL_MS / 2 - 1);
    const expectedTicks = 1 + Math.floor(elapsed / CIRCUIT_BREAKER_POLL_INTERVAL_MS);
    expect(getAgentCircuitBreaker).toHaveBeenCalledTimes(expectedTicks);
  });

  test('a fetch failure leaves the banner up and the next tick retries', async () => {
    const setCircuitOpen = vi.fn();
    getAgentCircuitBreaker.mockRejectedValueOnce(new Error('network'));
    renderHook(() => useCircuitBannerAutoClear(banner, setCircuitOpen, true));
    await vi.advanceTimersByTimeAsync(0);
    expect(setCircuitOpen).not.toHaveBeenCalled();
    await vi.advanceTimersByTimeAsync(CIRCUIT_BREAKER_POLL_INTERVAL_MS);
    expect(getAgentCircuitBreaker).toHaveBeenCalledTimes(2);
  });

  test('does not poll a cooling banner', async () => {
    const setCircuitOpen = vi.fn();
    renderHook(() => useCircuitBannerAutoClear({ agentId: 'ag_1', reason: 'cooling' }, setCircuitOpen, true));
    await vi.advanceTimersByTimeAsync(CIRCUIT_BREAKER_POLL_INTERVAL_MS * 2);
    expect(getAgentCircuitBreaker).not.toHaveBeenCalled();
    expect(setCircuitOpen).not.toHaveBeenCalled();
  });

  test('logged out: clears the banner and never polls the authenticated endpoint', async () => {
    const setCircuitOpen = vi.fn();
    const { rerender } = renderHook(
      ({ loggedIn }) => useCircuitBannerAutoClear(banner, setCircuitOpen, loggedIn),
      { initialProps: { loggedIn: true } },
    );
    await vi.advanceTimersByTimeAsync(0);
    expect(getAgentCircuitBreaker).toHaveBeenCalledTimes(1);

    rerender({ loggedIn: false });
    expect(setCircuitOpen).toHaveBeenCalledWith(null);
    await vi.advanceTimersByTimeAsync(CIRCUIT_BREAKER_POLL_INTERVAL_MS * 2);
    expect(getAgentCircuitBreaker).toHaveBeenCalledTimes(1); // no further polls
  });

  test('stops polling when the banner is gone', async () => {
    const setCircuitOpen = vi.fn();
    const { rerender } = renderHook(
      ({ b }) => useCircuitBannerAutoClear(b, setCircuitOpen, true),
      { initialProps: { b: banner as typeof banner | null } },
    );
    await vi.advanceTimersByTimeAsync(0);
    rerender({ b: null });
    await vi.advanceTimersByTimeAsync(CIRCUIT_BREAKER_POLL_INTERVAL_MS * 2);
    expect(getAgentCircuitBreaker).toHaveBeenCalledTimes(1);
  });
});
