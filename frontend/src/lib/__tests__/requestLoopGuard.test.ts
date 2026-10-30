/**
 * @file_name: requestLoopGuard.test.ts
 * @author: Bin Liang
 * @date: 2026-10-30
 * @description: The request-loop guard refuses an identical GET repeated past the window limit, keeps a spinning loop at zero network traffic until it goes quiet for a full window, holds failing reads under the edge failure line without freezing reads that work, and never touches writes.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { api, ApiError, RequestLoopBlockedError } from '../api';
import { __resetRequestLoopGuardForTests, guardedFetch } from '../guardedFetch';
import {
  delayUnlessAborted,
  isGuardedMethod,
  LOOP_BLOCK_DELAY_MS,
  LOOP_MAX_PER_WINDOW,
  LOOP_WINDOW_MS,
  PAGE_RAPID_MAX,
  FAIL_BLOCK_MS,
  FAIL_MAX,
  PAGE_FAIL_MAX,
  PAGE_WINDOW_MS,
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

  it('caps a loop spread over a few URLs, each under the per-URL limit, with the rapid budget', () => {
    // 6 URLs at 2/s each = 720/min, above the edge ban line, while every URL
    // stays at 10 per 5s (under LOOP_MAX_PER_WINDOW).
    let now = 0;
    const g = new RequestLoopGuard(() => now);
    let admitted = 0;
    for (let tick = 0; tick < 120; tick++) {
      for (let u = 0; u < 6; u++) if (g.check(`/api/channels/c${u}/credential`) === 'ok') admitted += 1;
      now += 500;
    }
    expect(admitted).toBeLessThanOrEqual(6 + PAGE_RAPID_MAX); // first read of each URL is not a repeat
  });

  it('ordinary polling is never refused, however many agents it covers', () => {
    // 300 agents' chat history every 15s plus the other background polls:
    // request volume alone is never judged, and none of it spends the rapid budget.
    let now = 0;
    const g = new RequestLoopGuard(() => now);
    const refused: string[] = [];
    for (let t = 0; t < 5 * 60_000; t += 1_000) {
      now = t;
      const due: string[] = [];
      if (t % 15_000 === 0) for (let a = 0; a < 300; a++) due.push(`/api/agents/agent_${a}/simple-chat-history?limit=5`);
      if (t % 10_000 === 0) due.push('/api/agent-inbox?agent_id=a', '/api/jobs?agent_id=a', '/api/teams');
      if (t % 30_000 === 0) due.push('/api/auth/agents', '/api/notices?unread_only=true');
      if (t % 3_000 === 0) due.push(`/api/teams/t1/messages?since=${t}`);
      for (const k of due) if (g.check(k) !== 'ok') refused.push(k);
    }
    expect(refused).toEqual([]);
  });

  it('a busy but human page is never refused', () => {
    // 281/min is the measured per-IP human peak (2026-10-28).
    let now = 0;
    const g = new RequestLoopGuard(() => now);
    for (let i = 0; i < 281 * 5; i++) {
      expect(g.check(`/api/y?i=${i % 40}`)).toBe('ok');
      now += 60_000 / 281;
    }
  });

  it("another tab's messages can at most spend a budget, never pile up beyond it", () => {
    let now = 0;
    const g = new RequestLoopGuard(() => now);
    for (let i = 0; i < 10 * PAGE_RAPID_MAX; i++) g.recordRemote('rapid');
    expect(g.spent('rapid')).toBe(PAGE_RAPID_MAX);
    now += PAGE_WINDOW_MS + 1;
    g.check('/api/after-burst');
    expect(g.check('/api/after-burst')).toBe('ok'); // a rapid repeat admitted a minute later, not ten
  });

  it('tabs share the rapid budget (review I-3)', () => {
    let now = 0;
    const tabs: RequestLoopGuard[] = [];
    for (let i = 0; i < 2; i++) {
      tabs.push(new RequestLoopGuard({
        now: () => now,
        onSpend: (kind) => tabs.filter((_, j) => j !== i).forEach((t) => t.recordRemote(kind)),
      }));
    }
    let admitted = 0;
    for (let tick = 0; tick < 200; tick++) {
      // each tab loops over 6 URLs at 2/s: every URL under the per-URL limit
      for (let u = 0; u < 6; u++) if (tabs[tick % 2].check(`/api/c${u}?tab=${tick % 2}`) === 'ok') admitted += 1;
      now += 250;
    }
    expect(admitted).toBeLessThanOrEqual(12 + PAGE_RAPID_MAX); // one shared budget, not one per tab
  });

  it('a URL that keeps failing is blocked for a minute, then released', () => {
    let now = 0;
    const g = new RequestLoopGuard(() => now);
    for (let i = 0; i < FAIL_MAX; i++) {
      expect(g.check('/api/agents/gone/awareness')).toBe('ok');
      g.recordFailure('/api/agents/gone/awareness');
      now += 1_000;
    }
    expect(g.check('/api/agents/gone/awareness')).toBe('failing');
    expect(g.check('/api/agents/other/awareness')).toBe('ok'); // per URL
    now += FAIL_BLOCK_MS;
    expect(g.check('/api/agents/gone/awareness')).toBe('ok');
  });

  it('a polling loop on a deleted resource stays far below the edge failure line', () => {
    // 6 URLs each re-read every 2s (a poll, so the per-URL layer never trips),
    // every read failing: the edge bans at 20 failures a minute per IP.
    // The edge judges each minute on its own, so the bound is on the worst
    // sliding 60s window, not an average.
    let now = 0;
    const g = new RequestLoopGuard(() => now);
    const sentAt: number[] = [];
    while (now < 10 * 60_000) {
      for (let u = 0; u < 6; u++) {
        const url = `/api/agents/gone/x${u}`;
        if (g.check(url) === 'ok') { sentAt.push(now); g.recordFailure(url); }
      }
      now += 2_000;
    }
    const worstMinute = Math.max(...sentAt.map((t) => sentAt.filter((u) => u >= t && u < t + 60_000).length));
    expect(worstMinute).toBeLessThanOrEqual(PAGE_FAIL_MAX);
  });

  it('while the failure budget is spent, paths that last answered 2xx keep working', () => {
    let now = 0;
    const g = new RequestLoopGuard(() => now);
    expect(g.check('/api/auth/agents')).toBe('ok');
    g.recordSuccess('/api/auth/agents');
    for (let i = 0; i < PAGE_FAIL_MAX; i++) {
      const url = `/api/agents/foreign/capability${i}`;
      g.check(url);
      g.recordFailure(url);
      now += 1_000;
    }
    now += 3_000;
    expect(g.check('/api/auth/agents')).toBe('ok'); // proven path: the app keeps working
    expect(g.check('/api/agents/foreign/capability0')).toBe('fail_budget'); // last answer a failure
    expect(g.check('/api/never/seen')).toBe('fail_budget'); // not known to work: fail-closed
  });

  it('a path is keyed with its agent_id, and relative and absolute spellings agree', () => {
    let now = 0;
    const g = new RequestLoopGuard(() => now);
    g.check('/api/jobs?agent_id=mine&status=all');
    g.recordSuccess('/api/jobs?agent_id=mine&status=all');
    for (let i = 0; i < PAGE_FAIL_MAX; i++) {
      g.check(`/api/jobs?agent_id=foreign&n=${i}`);
      g.recordFailure(`/api/jobs?agent_id=foreign&n=${i}`);
    }
    now += 3_000;
    // The viewer's own agent's jobs keep working, under either spelling.
    expect(g.check('/api/jobs?agent_id=mine&status=active')).toBe('ok');
    expect(g.check('http://localhost:8000/api/jobs?agent_id=mine')).toBe('ok');
    expect(g.check('/api/jobs?agent_id=foreign&n=99')).toBe('fail_budget');
  });

  it('a path that worked once but now fails is refused even when the query changes', () => {
    let now = 0;
    const g = new RequestLoopGuard(() => now);
    g.check('/api/search?q=0');
    g.recordSuccess('/api/search?q=0');
    let sent = 0;
    for (let i = 1; i < 300; i++) {
      const url = `/api/search?q=${i}`;
      if (g.check(url) === 'ok') { sent += 1; g.recordFailure(url); }
      now += 200;
    }
    expect(sent).toBe(PAGE_FAIL_MAX);
  });

  it('a 404 loop with a changing query is held to the page failure budget', () => {
    // Every URL is new, so neither per-URL layer sees it; only the page budget does.
    let now = 0;
    const g = new RequestLoopGuard(() => now);
    let sent = 0;
    for (let i = 0; i < 600; i++) {
      const url = `/api/agents/gone/files?nonce=${i}`;
      if (g.check(url) === 'ok') { sent += 1; g.recordFailure(url); }
      now += 100; // 10/s for a minute
    }
    expect(sent).toBe(PAGE_FAIL_MAX);
    now += PAGE_WINDOW_MS;
    expect(g.check('/api/after')).toBe('ok'); // reads resume once the minute passes
  });

  it('a real-user failure pace (6 a minute) never pauses reads', () => {
    let now = 0;
    const g = new RequestLoopGuard(() => now);
    for (let i = 0; i < 60; i++) {
      const url = `/api/browser/notices/agent_${i}`;
      expect(g.check(url)).toBe('ok');
      if (i % 10 === 0) g.recordFailure(url); // one every 10 s = 6 a minute
      now += 1_000;
    }
  });

  it('occasional failures spread out never block', () => {
    let now = 0;
    const g = new RequestLoopGuard(() => now);
    for (let i = 0; i < 50; i++) {
      expect(g.check('/api/flaky')).toBe('ok');
      g.recordFailure('/api/flaky');
      now += 7_000; // never FAIL_MAX inside one window, and under the page budget
    }
  });

  it('a request refused by a page budget does not count toward its URL window (review M-3)', () => {
    let now = 0;
    const g = new RequestLoopGuard(() => now);
    // Spend the rapid budget: 15 URLs × 10 quick repeats each (each stays under
    // the per-URL limit).
    for (let k = 0; k < 15; k++) for (let r = 0; r <= 10; r++) { g.check(`/api/fill/${k}`); now += 1; }
    expect(g.check('/api/z')).toBe('ok'); // first read: not a repeat
    for (let i = 0; i < LOOP_MAX_PER_WINDOW * 2; i++) {
      // Rapid repeats, all refused by the budget. Were refusals counted toward
      // the URL's window, this would flip to 'loop' after 15.
      expect(g.check('/api/z')).toBe('rapid_budget');
    }
  });

  it('a block that expires untouched and is then pruned keeps its real release time (review M-2)', () => {
    const run = (gapAfterBlockMs: number) => {
      let now = 0;
      const g = new RequestLoopGuard(() => now);
      const trip = () => { for (let i = 0; i <= LOOP_MAX_PER_WINDOW; i++) g.check('/api/hot'); };
      trip(); // 5s block, then no attempts at all
      now += LOOP_WINDOW_MS + 1;
      for (let i = 0; i < 600; i++) { g.check(`/api/once?i=${i}`); now += 1; } // forces a prune
      now += gapAfterBlockMs;
      trip();
      const start = now;
      while (g.check('/api/hot') === 'loop') now += 2_500; // > LOOP_EXTEND_GAP_MS: never extends
      return now - start;
    };
    // Without the backfill the pruned strike keeps an infinite release time and
    // a trip 70s later would wrongly escalate. (Quick repeats escalating is
    // covered by the backoff test above.)
    expect(run(70_000)).toBeLessThan(2 * LOOP_WINDOW_MS);
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

  it('guards reads (GET, HEAD) only', () => {
    expect(isGuardedMethod(undefined)).toBe(true);
    expect(isGuardedMethod('get')).toBe(true);
    expect(isGuardedMethod('HEAD')).toBe(true);
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
  beforeEach(() => __resetRequestLoopGuardForTests());
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

describe('guardedFetch', () => {
  beforeEach(() => __resetRequestLoopGuardForTests());
  afterEach(() => {
    vi.useRealTimers();
    vi.restoreAllMocks();
  });

  it('returns the Response untouched, error statuses included', async () => {
    const res = new Response('gone', { status: 410 });
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(res);
    expect(await guardedFetch('/api/artifacts/raw/x', { method: 'HEAD' })).toBe(res);
  });

  it('refuses a raw-fetch loop with a localized message that names no URL (review I-1, I-4)', async () => {
    vi.useFakeTimers();
    const fetchSpy = vi.spyOn(globalThis, 'fetch').mockImplementation(async () => new Response('{}'));
    vi.spyOn(console, 'error').mockImplementation(() => {});
    const url = '/api/agents/agent_secret/artifacts?scope=context';
    for (let i = 0; i < LOOP_MAX_PER_WINDOW; i++) await guardedFetch(url);
    const blocked = guardedFetch(url);
    const settled = expect(blocked).rejects.toBeInstanceOf(RequestLoopBlockedError);
    await vi.advanceTimersByTimeAsync(LOOP_BLOCK_DELAY_MS);
    await settled;
    expect(fetchSpy).toHaveBeenCalledTimes(LOOP_MAX_PER_WINDOW);
    const err = await blocked.catch((e: Error) => e);
    expect(err.message).not.toContain('/api/');
    expect(err.message).not.toContain('agent_secret');
    expect(err.message).not.toBe('common.requestBlocked'); // resolved, not a raw key
  });
});

describe('guardedFetch across tabs (pre-push review I-C)', () => {
  // The real BroadcastChannel (Node provides one): the production wiring, not
  // the class methods.
  const CHANNEL = 'narranexus.request-loop-guard';
  let other: BroadcastChannel;
  beforeEach(() => {
    __resetRequestLoopGuardForTests();
    other = new BroadcastChannel(CHANNEL);
  });
  afterEach(() => {
    other.close();
    vi.useRealTimers();
    vi.restoreAllMocks();
  });

  it("another tab's rapid repeats spend this tab's rapid budget", async () => {
    vi.spyOn(globalThis, 'fetch').mockImplementation(async () => new Response('{}'));
    const report = vi.spyOn(console, 'error').mockImplementation(() => {});
    for (let i = 0; i < PAGE_RAPID_MAX; i++) other.postMessage('rapid');
    await new Promise((r) => setTimeout(r, 50)); // let the messages land
    await guardedFetch('/api/one-more'); // first read: not a repeat
    vi.useFakeTimers();
    const refused = guardedFetch('/api/one-more'); // rapid repeat, budget spent elsewhere
    const settled = expect(refused).rejects.toBeInstanceOf(RequestLoopBlockedError);
    await vi.advanceTimersByTimeAsync(LOOP_BLOCK_DELAY_MS);
    await settled;
    expect(String(report.mock.calls[0][0])).toContain(`${PAGE_RAPID_MAX} rapid repeat reads`);
  });

  it("another tab's failures spend this tab's failure budget", async () => {
    vi.spyOn(globalThis, 'fetch').mockImplementation(async () => new Response('{}'));
    vi.spyOn(console, 'error').mockImplementation(() => {});
    for (let i = 0; i < PAGE_FAIL_MAX; i++) other.postMessage('fail');
    await new Promise((r) => setTimeout(r, 50));
    vi.useFakeTimers();
    const refused = guardedFetch('/api/healthy');
    const settled = expect(refused).rejects.toBeInstanceOf(RequestLoopBlockedError);
    await vi.advanceTimersByTimeAsync(LOOP_BLOCK_DELAY_MS);
    await settled;
  });

  it("this tab's rapid repeats are announced to the other tabs", async () => {
    vi.spyOn(globalThis, 'fetch').mockImplementation(async () => new Response('{}'));
    const seen: unknown[] = [];
    other.onmessage = (e: MessageEvent<unknown>) => seen.push(e.data);
    await guardedFetch('/api/announced');
    await guardedFetch('/api/announced');
    await new Promise((r) => setTimeout(r, 50));
    expect(seen).toEqual(['rapid']);
  });
});

describe('guardedFetch reports failing reads', () => {
  beforeEach(() => __resetRequestLoopGuardForTests());
  afterEach(() => {
    vi.useRealTimers();
    vi.restoreAllMocks();
  });

  it('a read that keeps answering 404 stops reaching the network', async () => {
    vi.useFakeTimers();
    const fetchSpy = vi.spyOn(globalThis, 'fetch').mockImplementation(async () => new Response('', { status: 404 }));
    vi.spyOn(console, 'error').mockImplementation(() => {});
    for (let i = 0; i < FAIL_MAX; i++) {
      await guardedFetch('/api/agents/gone/awareness');
      await vi.advanceTimersByTimeAsync(3_000); // a 3s poll: never trips the per-URL layer
    }
    const refused = guardedFetch('/api/agents/gone/awareness');
    const settled = expect(refused).rejects.toBeInstanceOf(RequestLoopBlockedError);
    await vi.advanceTimersByTimeAsync(LOOP_BLOCK_DELAY_MS);
    await settled;
    expect(fetchSpy).toHaveBeenCalledTimes(FAIL_MAX);
  });

  it('a read that answered 2xx keeps working while other reads exhaust the failure budget', async () => {
    const fetchSpy = vi.spyOn(globalThis, 'fetch').mockImplementation(async (input) =>
      new Response('', { status: String(input).startsWith('/api/auth/agents') ? 200 : 403 }));
    vi.spyOn(console, 'error').mockImplementation(() => {});
    await guardedFetch('/api/auth/agents');
    for (let i = 0; i < PAGE_FAIL_MAX; i++) await guardedFetch(`/api/agents/foreign/p${i}`);
    const res = await guardedFetch('/api/auth/agents');
    expect(res.status).toBe(200);
    expect(fetchSpy).toHaveBeenCalledTimes(PAGE_FAIL_MAX + 2);
  });

  it('once a read has failed, concurrent first reads cannot overshoot the failure budget', async () => {
    // A panel switch to an agent the viewer may not read fires many first reads
    // at once; failures are only known when they land.
    const fetchSpy = vi.spyOn(globalThis, 'fetch').mockImplementation(
      () => new Promise((resolve) => setTimeout(() => resolve(new Response('', { status: 403 })), 20)),
    );
    vi.spyOn(console, 'error').mockImplementation(() => {});
    await guardedFetch('/api/agents/foreign/first');
    const burst = Array.from({ length: 25 }, (_, i) => guardedFetch(`/api/agents/foreign/panel${i}`).catch(() => null));
    await Promise.all(burst);
    expect(fetchSpy.mock.calls.length).toBeLessThanOrEqual(PAGE_FAIL_MAX);
  });

  it('on a clean page a burst of first reads is never held back', async () => {
    const fetchSpy = vi.spyOn(globalThis, 'fetch').mockImplementation(
      () => new Promise((resolve) => setTimeout(() => resolve(new Response('{}', { status: 200 })), 20)),
    );
    await Promise.all(Array.from({ length: 40 }, (_, i) => guardedFetch(`/api/first/load${i}`)));
    expect(fetchSpy).toHaveBeenCalledTimes(40);
  });

  it('reads that settle by network error or abort release their pending slot', async () => {
    vi.spyOn(console, 'error').mockImplementation(() => {});
    const fetchSpy = vi.spyOn(globalThis, 'fetch')
      .mockResolvedValueOnce(new Response('', { status: 404 }))
      .mockRejectedValue(new TypeError('network'));
    await guardedFetch('/api/one/failure');
    for (let i = 0; i < 30; i++) await guardedFetch(`/api/flaky/net${i}`).catch(() => null);
    // 1 failure + 30 settled network errors: nothing is left pending, so reads still go out.
    expect(fetchSpy).toHaveBeenCalledTimes(31);
  });

  it.each([200, 401, 429, 500])('status %i is not a failure for the guard', async (status) => {
    const fetchSpy = vi.spyOn(globalThis, 'fetch').mockImplementation(async () => new Response('', { status }));
    for (let i = 0; i < PAGE_FAIL_MAX * 2; i++) await guardedFetch(`/api/s${status}?i=${i}`);
    expect(fetchSpy).toHaveBeenCalledTimes(PAGE_FAIL_MAX * 2);
  });

  it('failures are announced to the other tabs', async () => {
    vi.spyOn(globalThis, 'fetch').mockImplementation(async () => new Response('', { status: 404 }));
    const other = new BroadcastChannel('narranexus.request-loop-guard');
    const seen: unknown[] = [];
    other.onmessage = (e: MessageEvent<unknown>) => seen.push(e.data);
    await guardedFetch('/api/agents/gone');
    await new Promise((r) => setTimeout(r, 50));
    other.close();
    expect(seen).toContain('fail');
  });

  it('failing writes are never counted', async () => {
    const fetchSpy = vi.spyOn(globalThis, 'fetch').mockImplementation(async () => new Response('', { status: 404 }));
    for (let i = 0; i < FAIL_MAX * 3; i++) await guardedFetch('/api/w', { method: 'POST' });
    await guardedFetch('/api/w');
    expect(fetchSpy).toHaveBeenCalledTimes(FAIL_MAX * 3 + 1);
  });
});
