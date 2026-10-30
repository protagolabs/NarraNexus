/**
 * @file_name: requestLoopGuard.test.ts
 * @author: Bin Liang
 * @date: 2026-10-30
 * @description: The request-loop guard refuses an identical GET repeated past the window limit, keeps a spinning loop at zero network traffic until it goes quiet for a full window, caps the page-wide GET rate, and never touches writes.
 */
import { afterEach, describe, expect, it, vi } from 'vitest';

import { api, ApiError, RequestLoopBlockedError } from '../api';
import {
  delayUnlessAborted,
  isGuardedMethod,
  LOOP_BLOCK_DELAY_MS,
  LOOP_MAX_PER_WINDOW,
  LOOP_WINDOW_MS,
  PAGE_GET_MAX_PER_WINDOW,
  PAGE_GET_WINDOW_MS,
  RequestLoopGuard,
} from '../requestLoopGuard';

describe('RequestLoopGuard', () => {
  it('admits up to the limit inside one window, then refuses', () => {
    let now = 0;
    const g = new RequestLoopGuard(() => now);
    for (let i = 0; i < LOOP_MAX_PER_WINDOW; i++) {
      expect(g.check('/api/x')).toBe('ok');
      now += 10;
    }
    expect(g.check('/api/x')).toBe('loop');
  });

  it('a steady 2s poll is never refused', () => {
    let now = 0;
    const g = new RequestLoopGuard(() => now);
    for (let i = 0; i < 200; i++) {
      expect(g.check('/api/notices')).toBe('ok');
      now += 2_000;
    }
  });

  it('a serial loop slowed by the block delay leaks nothing to the network', () => {
    // The shape that matters (review I-1): request → 30ms latency → setState →
    // refetch. Once refused, each attempt settles LOOP_BLOCK_DELAY_MS later, so
    // the loop retries about once a second. A plain sliding window would drain
    // and let a fresh burst through every few seconds.
    let now = 0;
    const g = new RequestLoopGuard(() => now);
    let admittedAfterBlock = 0;
    let blocked = false;
    while (now < 60_000) {
      const v = g.check('/api/channels/lark/credential?agent_id=a');
      if (v === 'ok') {
        if (blocked) admittedAfterBlock += 1;
        now += 30;
      } else {
        blocked = true;
        now += LOOP_BLOCK_DELAY_MS;
      }
    }
    expect(blocked).toBe(true);
    expect(admittedAfterBlock).toBe(0);
  });

  it('a 2s poller that got caught once is released, not blocked forever', () => {
    // Its attempts are 3s apart while blocked (2s interval + 1s refusal delay),
    // wider than LOOP_EXTEND_GAP_MS, so they do not keep extending the block.
    let now = 0;
    const g = new RequestLoopGuard(() => now);
    for (let i = 0; i <= LOOP_MAX_PER_WINDOW; i++) g.check('/api/notices'); // a brief burst trips it
    let admitted = false;
    for (let i = 0; i < 10 && !admitted; i++) {
      now += 2_000 + LOOP_BLOCK_DELAY_MS;
      admitted = g.check('/api/notices') === 'ok';
    }
    expect(admitted).toBe(true);
  });

  it('a loop that backs off on failure but refetches at once on success is starved', () => {
    // Review M-1 shape: success → refetch after 50ms; refusal → 1s delay + 1s
    // backoff (2s apart, so blocked attempts never extend the block). Without
    // escalation it leaked ~135 requests/min; repeat offenders now get 5, 10,
    // 20, 40, then 60s blocks.
    let now = 0;
    const g = new RequestLoopGuard(() => now);
    let admittedInLastMinute = 0;
    while (now < 5 * 60_000) {
      const ok = g.check('/api/x') === 'ok';
      if (ok && now >= 4 * 60_000) admittedInLastMinute += 1;
      now += ok ? 50 : LOOP_BLOCK_DELAY_MS + 1_000;
    }
    expect(admittedInLastMinute).toBeLessThanOrEqual(LOOP_MAX_PER_WINDOW * 2);
  });

  it('a trip long after the previous block ended is a first offence again', () => {
    let now = 0;
    const g = new RequestLoopGuard(() => now);
    for (let i = 0; i <= LOOP_MAX_PER_WINDOW; i++) g.check('/api/x'); // trip once (5s block)
    now += 10 * 60_000; // idle 10 min, no attempts in between
    for (let i = 0; i <= LOOP_MAX_PER_WINDOW; i++) g.check('/api/x'); // trip again
    now += LOOP_WINDOW_MS - 1;
    expect(g.check('/api/x')).toBe('loop'); // gap > 2s: not extended
    now += 2;
    expect(g.check('/api/x')).toBe('ok'); // a plain 5s block, not an escalated 10s one
  });

  it('releases a URL once it has been quiet for a full window', () => {
    let now = 0;
    const g = new RequestLoopGuard(() => now);
    for (let i = 0; i <= LOOP_MAX_PER_WINDOW; i++) g.check('/api/x');
    now += LOOP_WINDOW_MS - 1;
    expect(g.check('/api/x')).toBe('loop'); // still spinning: block extended
    now += LOOP_WINDOW_MS + 1;
    expect(g.check('/api/x')).toBe('ok');
  });

  it('counts URLs independently', () => {
    const g = new RequestLoopGuard(() => 0);
    for (let i = 0; i <= LOOP_MAX_PER_WINDOW; i++) g.check('/api/a');
    expect(g.check('/api/a')).toBe('loop');
    expect(g.check('/api/b')).toBe('ok');
  });

  it('caps the page-wide GET rate, and recovers as the real rate drops', () => {
    // A loop with a changing query never repeats a URL; the page budget holds it.
    let now = 0;
    const g = new RequestLoopGuard(() => now);
    let admitted = 0;
    for (let i = 0; i < 2_000; i++) {
      if (g.check(`/api/x?t=${i}`) === 'ok') admitted += 1;
      now += 10; // 100 distinct GETs/s for 20s
    }
    expect(admitted).toBe(PAGE_GET_MAX_PER_WINDOW);
    now += PAGE_GET_WINDOW_MS;
    expect(g.check('/api/after')).toBe('ok');
  });

  it('a busy but human page stays under the page budget', () => {
    // 281/min is the measured per-IP human peak (2026-10-28).
    let now = 0;
    const g = new RequestLoopGuard(() => now);
    for (let i = 0; i < 281 * 5; i++) {
      expect(g.check(`/api/y?i=${i % 40}`)).toBe('ok');
      now += 60_000 / 281;
    }
  });

  it('reports a key at most once per minute', () => {
    let now = 0;
    const g = new RequestLoopGuard(() => now);
    expect(g.shouldReport('/api/x')).toBe(true);
    now += 30_000;
    expect(g.shouldReport('/api/x')).toBe(false);
    now += 31_000;
    expect(g.shouldReport('/api/x')).toBe(true);
  });

  it('guards GET only', () => {
    expect(isGuardedMethod(undefined)).toBe(true);
    expect(isGuardedMethod('get')).toBe(true);
    for (const m of ['POST', 'PUT', 'PATCH', 'DELETE']) expect(isGuardedMethod(m)).toBe(false);
  });

  it('the block delay rejects early when the caller aborts', async () => {
    const ctl = new AbortController();
    const p = delayUnlessAborted(60_000, ctl.signal);
    ctl.abort();
    await expect(p).rejects.toMatchObject({ name: 'AbortError' });
  });
});

describe('api.request with the loop guard', () => {
  afterEach(() => {
    vi.useRealTimers();
    vi.restoreAllMocks();
  });

  const ok = () =>
    vi.spyOn(globalThis, 'fetch').mockImplementation(async () => ({
      ok: true,
      status: 200,
      json: async () => ({ success: true, data: null }),
    }) as unknown as Response);

  it('a GET loop stops reaching the network after the limit and fails with RequestLoopBlockedError', async () => {
    vi.useFakeTimers();
    const fetchSpy = ok();
    vi.spyOn(console, 'error').mockImplementation(() => {});
    const agent = `agent_loop_${Math.random()}`;
    for (let i = 0; i < LOOP_MAX_PER_WINDOW; i++) await api.channelCredential('lark', agent);
    expect(fetchSpy).toHaveBeenCalledTimes(LOOP_MAX_PER_WINDOW);

    const blocked = api.channelCredential('lark', agent);
    const settled = expect(blocked).rejects.toBeInstanceOf(RequestLoopBlockedError);
    await vi.advanceTimersByTimeAsync(LOOP_BLOCK_DELAY_MS);
    await settled;
    expect(fetchSpy).toHaveBeenCalledTimes(LOOP_MAX_PER_WINDOW);
    // Still an ApiError, so existing `instanceof ApiError` handlers keep working.
    await expect(blocked).rejects.toBeInstanceOf(ApiError);
  });

  it('the refusal settles only after the block delay, throttling a retry-on-failure loop', async () => {
    vi.useFakeTimers();
    ok();
    vi.spyOn(console, 'error').mockImplementation(() => {});
    const agent = `agent_delay_${Math.random()}`;
    for (let i = 0; i < LOOP_MAX_PER_WINDOW; i++) await api.channelCredential('slack', agent);
    let done = false;
    const p = api.channelCredential('slack', agent).catch(() => { done = true; });
    await vi.advanceTimersByTimeAsync(LOOP_BLOCK_DELAY_MS - 1);
    expect(done).toBe(false);
    await vi.advanceTimersByTimeAsync(1);
    await p;
    expect(done).toBe(true);
  });

  it('writes to the same URL are never refused', async () => {
    const fetchSpy = ok();
    const agent = `agent_write_${Math.random()}`;
    for (let i = 0; i < LOOP_MAX_PER_WINDOW * 2; i++) await api.updateJob(`job_${agent}`, agent, { payload: 'x' });
    expect(fetchSpy).toHaveBeenCalledTimes(LOOP_MAX_PER_WINDOW * 2);
  });
});
