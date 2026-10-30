/**
 * @file_name: requestLoopGuard.test.ts
 * @author: Bin Liang
 * @date: 2026-10-30
 * @description: The request-loop guard refuses an identical GET repeated past the window limit, keeps a spinning loop at zero network traffic until it goes quiet for a full window, holds failing reads under the edge failure line without freezing reads that work, and never touches writes.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { api, ApiError, RequestLoopBlockedError } from '../api';
import { __resetRequestLoopGuardForTests, guardedFetch } from '../guardedFetch';
import { useRuntimeStore } from '@/stores/runtimeStore';
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
  EDGE_FAIL_SAFE,
  SLOT_WAIT_MAX_MS,
  PAGE_WINDOW_MS,
  RequestLoopGuard,
} from '../requestLoopGuard';

// Behind the edge by default: jsdom's origin is localhost, which guardedFetch
// would read as a local backend (page-wide failure limits off).
beforeEach(() => useRuntimeStore.setState({ mode: 'cloud-web' }));
afterEach(() => useRuntimeStore.setState({ mode: null }));

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

  it('a queued read is re-judged when it is served: a path proven meanwhile needs no slot', async () => {
    let now = 0;
    const g = new RequestLoopGuard({ now: () => now });
    const held: Array<() => void> = [];
    for (let i = 0; i < EDGE_FAIL_SAFE; i++) {
      const r = await g.acquire(`/api/held/${i}`);
      if (typeof r === 'function') held.push(r);
    }
    expect(held).toHaveLength(EDGE_FAIL_SAFE);
    const queued = g.acquire('/api/agents/list');
    for (let i = 0; i < PAGE_FAIL_MAX; i++) g.recordFailure(`/api/elsewhere/${i}`); // budget spent
    g.recordSuccess('/api/agents/list?from=another-read'); // its path is proven meanwhile
    held[0]();
    expect(typeof (await queued)).toBe('function'); // served, not refused as fail_budget
  });

  it('dispose refuses queued reads instead of leaving them pending', async () => {
    const g = new RequestLoopGuard();
    for (let i = 0; i < EDGE_FAIL_SAFE; i++) await g.acquire(`/api/held/${i}`);
    const queued = g.acquire('/api/q');
    g.dispose();
    expect(await queued).toBe('slot_wait');
  });

  it('a queued read is reported once and leaves no timer behind once served', async () => {
    vi.useFakeTimers();
    const onSlotWait = vi.fn();
    const g = new RequestLoopGuard({ onSlotWait });
    const held: Array<() => void> = [];
    for (let i = 0; i < EDGE_FAIL_SAFE; i++) {
      const r = await g.acquire(`/api/held/${i}`);
      if (typeof r === 'function') held.push(r);
    }
    expect(onSlotWait).not.toHaveBeenCalled(); // granted at once: no wait report
    const queued = g.acquire('/api/q');
    expect(onSlotWait).toHaveBeenCalledTimes(1);
    held[0]();
    expect(typeof (await queued)).toBe('function');
    expect(vi.getTimerCount()).toBe(0);
    vi.useRealTimers();
  });

  it('a queued read is served without a slot once the edge limits are switched off', async () => {
    let enabled = true;
    const g = new RequestLoopGuard({ edgeLimitsEnabled: () => enabled });
    const held: Array<() => void> = [];
    for (let i = 0; i < EDGE_FAIL_SAFE; i++) {
      const r = await g.acquire(`/api/held/${i}`);
      if (typeof r === 'function') held.push(r);
    }
    const queued = g.acquire('/api/q');
    for (let i = 0; i < PAGE_FAIL_MAX; i++) g.recordFailure(`/api/elsewhere/${i}`);
    enabled = false; // the mode resolved to local
    held[0]();
    expect(typeof (await queued)).toBe('function');
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

  it('while the failure budget is spent, paths whose last answer was a success keep working', () => {
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

describe('guardedFetch in-flight slots and runtime mode', () => {
  beforeEach(() => __resetRequestLoopGuardForTests());
  afterEach(() => {
    vi.useRealTimers();
    vi.restoreAllMocks();
  });

  function slowFetch(status: (url: string) => number) {
    let inflight = 0;
    let peak = 0;
    const spy = vi.spyOn(globalThis, 'fetch').mockImplementation(async (input) => {
      inflight += 1;
      peak = Math.max(peak, inflight);
      await new Promise((r) => setTimeout(r, 10));
      inflight -= 1;
      return new Response('{}', { status: status(String(input)) });
    });
    return { spy, peak: () => peak };
  }

  it('a crafted burst of foreign reads on a clean page stays under the edge line', async () => {
    // e.g. a link listing 50 foreign agents x 5 preview reads each, all 403.
    const { spy, peak } = slowFetch(() => 403);
    vi.spyOn(console, 'error').mockImplementation(() => {});
    await Promise.all(Array.from({ length: 250 }, (_, i) => guardedFetch(`/api/agents/x${i}/files`).catch(() => null)));
    expect(spy.mock.calls.length).toBeLessThanOrEqual(EDGE_FAIL_SAFE);
    expect(peak()).toBeLessThanOrEqual(EDGE_FAIL_SAFE);
  });

  it('a clean burst of first reads all goes out, at most EDGE_FAIL_SAFE at a time', async () => {
    const { spy, peak } = slowFetch(() => 200);
    await Promise.all(Array.from({ length: 40 }, (_, i) => guardedFetch(`/api/first/load${i}`)));
    expect(spy).toHaveBeenCalledTimes(40);
    expect(peak()).toBeLessThanOrEqual(EDGE_FAIL_SAFE);
  });

  it('a page-budget refusal is immediate, not delayed', async () => {
    vi.spyOn(globalThis, 'fetch').mockImplementation(async () => new Response('', { status: 404 }));
    vi.spyOn(console, 'error').mockImplementation(() => {});
    for (let i = 0; i < PAGE_FAIL_MAX; i++) await guardedFetch(`/api/gone/${i}`);
    vi.useFakeTimers();
    let settled = false;
    const refused = guardedFetch('/api/never/seen').catch((e) => { settled = true; return e; });
    await vi.advanceTimersByTimeAsync(0);
    expect(settled).toBe(true);
    expect(await refused).toBeInstanceOf(RequestLoopBlockedError);
  });

  it('a read waiting for a slot rejects on abort and frees nothing it did not hold', async () => {
    let release!: () => void;
    const gate = new Promise<void>((r) => { release = r; });
    vi.spyOn(globalThis, 'fetch').mockImplementation(async () => { await gate; return new Response('{}'); });
    const held = Array.from({ length: EDGE_FAIL_SAFE }, (_, i) => guardedFetch(`/api/held/${i}`));
    const controller = new AbortController();
    const waiting = guardedFetch('/api/waiting', { signal: controller.signal });
    controller.abort();
    await expect(waiting).rejects.toMatchObject({ name: 'AbortError' });
    release();
    await Promise.all(held);
    const after = await guardedFetch('/api/after');
    expect(after.status).toBe(200);
  });

  it('a read that finds no slot within SLOT_WAIT_MAX_MS is refused, never let through', async () => {
    vi.useFakeTimers();
    vi.spyOn(console, 'error').mockImplementation(() => {});
    vi.spyOn(console, 'warn').mockImplementation(() => {});
    const spy = vi.spyOn(globalThis, 'fetch').mockImplementation(() => new Promise(() => {})); // a hung backend
    for (let i = 0; i < EDGE_FAIL_SAFE; i++) void guardedFetch(`/api/hung/${i}`);
    const waiting = guardedFetch('/api/waiting').catch((e) => e);
    await vi.advanceTimersByTimeAsync(SLOT_WAIT_MAX_MS + 1);
    expect(await waiting).toBeInstanceOf(RequestLoopBlockedError);
    expect(spy).toHaveBeenCalledTimes(EDGE_FAIL_SAFE);
  });

  it('queued reads are served first come, first served', async () => {
    const order: string[] = [];
    let release!: () => void;
    const gate = new Promise<void>((r) => { release = r; });
    vi.spyOn(globalThis, 'fetch').mockImplementation(async (input) => {
      order.push(String(input));
      if (String(input).startsWith('/api/held')) await gate;
      return new Response('{}');
    });
    vi.spyOn(console, 'warn').mockImplementation(() => {});
    const held = Array.from({ length: EDGE_FAIL_SAFE }, (_, i) => guardedFetch(`/api/held/${i}`));
    const queued = ['/api/q/1', '/api/q/2', '/api/q/3'].map((u) => guardedFetch(u));
    release();
    await Promise.all([...held, ...queued]);
    expect(order.slice(EDGE_FAIL_SAFE)).toEqual(['/api/q/1', '/api/q/2', '/api/q/3']);
  });

  it('a queued read is re-checked when the oldest failure leaves the window', async () => {
    vi.useFakeTimers();
    vi.spyOn(console, 'error').mockImplementation(() => {});
    vi.spyOn(console, 'warn').mockImplementation(() => {});
    const spy = vi.spyOn(globalThis, 'fetch').mockImplementation(async (input) => {
      const u = String(input);
      if (u.startsWith('/api/gone')) return new Response('', { status: 404 });
      if (u.startsWith('/api/held')) return new Promise<Response>(() => {}); // never settles
      return new Response('{}');
    });
    for (let i = 0; i < 9; i++) await guardedFetch(`/api/gone/${i}`); // 9 failures, under the budget
    await vi.advanceTimersByTimeAsync(PAGE_WINDOW_MS - 5_000); // they expire 5s from now
    for (let i = 0; i < EDGE_FAIL_SAFE - 9; i++) void guardedFetch(`/api/held/${i}`); // 9 + 9 = 18: full
    await vi.advanceTimersByTimeAsync(0);
    expect(spy).toHaveBeenCalledTimes(EDGE_FAIL_SAFE);
    const waiting = guardedFetch('/api/waiting');
    // No slot is ever released; only the failures expiring (before the 15s
    // wait limit) can let it through.
    await vi.advanceTimersByTimeAsync(6_000);
    expect((await waiting).status).toBe(200);
  });

  it('a newcomer never overtakes a queued read when capacity frees without a release', async () => {
    vi.useFakeTimers();
    vi.spyOn(console, 'error').mockImplementation(() => {});
    vi.spyOn(console, 'warn').mockImplementation(() => {});
    const order: string[] = [];
    vi.spyOn(globalThis, 'fetch').mockImplementation(async (input) => {
      const u = String(input);
      order.push(u);
      if (u.startsWith('/api/gone')) return new Response('', { status: 404 });
      if (u.startsWith('/api/held')) return new Promise<Response>(() => {});
      return new Response('{}');
    });
    for (let i = 0; i < 9; i++) await guardedFetch(`/api/gone/${i}`);
    await vi.advanceTimersByTimeAsync(PAGE_WINDOW_MS - 5_000);
    for (let i = 0; i < EDGE_FAIL_SAFE - 9; i++) void guardedFetch(`/api/held/${i}`);
    await vi.advanceTimersByTimeAsync(0);
    const a = guardedFetch('/api/queued-a');
    // The failures leave the window now; the expiry wake fires 1ms later. A
    // newcomer arriving in between must queue behind A, not take the room.
    await vi.advanceTimersByTimeAsync(5_000);
    const b = guardedFetch('/api/newcomer-b');
    await vi.advanceTimersByTimeAsync(10);
    const tail = order.filter((u) => u === '/api/queued-a' || u === '/api/newcomer-b');
    expect(tail).toEqual(['/api/queued-a', '/api/newcomer-b']); // both out, in order — no wait limit needed
    await Promise.all([a, b]);
  });

  it("a queued read whose URL got fail-blocked meanwhile is refused and never sent", async () => {
    vi.spyOn(console, 'error').mockImplementation(() => {});
    vi.spyOn(console, 'warn').mockImplementation(() => {});
    let release!: () => void;
    const gate = new Promise<void>((r) => { release = r; });
    const calls: string[] = [];
    vi.spyOn(globalThis, 'fetch').mockImplementation(async (input) => {
      const u = String(input);
      calls.push(u);
      await gate;
      return new Response('', { status: u === '/api/k' ? 404 : 200 });
    });
    // Fill every slot: 3 reads of K (which will 404) plus filler reads.
    const first = [0, 1, 2].map(() => guardedFetch('/api/k'));
    const filler = Array.from({ length: EDGE_FAIL_SAFE - 3 }, (_, i) => guardedFetch(`/api/fill/${i}`));
    const queued = guardedFetch('/api/k').catch((e) => e);
    release();
    await Promise.all([...first, ...filler]);
    expect(await queued).toBeInstanceOf(RequestLoopBlockedError);
    expect(calls.filter((u) => u === '/api/k')).toHaveLength(3);
  });

  it('a status-0 (opaque) answer does not prove a path', async () => {
    vi.spyOn(console, 'error').mockImplementation(() => {});
    vi.spyOn(globalThis, 'fetch').mockImplementation(async (input) =>
      String(input).startsWith('/api/opaque')
        ? Object.defineProperty(new Response(null), 'status', { value: 0 })
        : new Response('', { status: 404 }));
    await guardedFetch('/api/opaque');
    for (let i = 0; i < PAGE_FAIL_MAX; i++) await guardedFetch(`/api/gone/${i}`);
    await expect(guardedFetch('/api/opaque')).rejects.toBeInstanceOf(RequestLoopBlockedError);
  });

  it('a local backend has no page-wide limits (no edge in front of it)', async () => {
    useRuntimeStore.setState({ mode: 'local' });
    __resetRequestLoopGuardForTests();
    const spy = vi.spyOn(globalThis, 'fetch').mockImplementation(async () => new Response('', { status: 404 }));
    for (let i = 0; i < PAGE_FAIL_MAX * 2; i++) await guardedFetch(`/api/excluded/plugin${i}`);
    expect(spy).toHaveBeenCalledTimes(PAGE_FAIL_MAX * 2);
  });

  it('an unknown mode with a non-loopback API keeps the limits on (fail-closed)', async () => {
    useRuntimeStore.setState({ mode: null });
    const win = window as unknown as { __NARRANEXUS_CONFIG__?: unknown };
    win.__NARRANEXUS_CONFIG__ = { apiUrl: 'https://agent.example.com' }; // mode left unset
    try {
      vi.spyOn(globalThis, 'fetch').mockImplementation(async () => new Response('', { status: 404 }));
      vi.spyOn(console, 'error').mockImplementation(() => {});
      for (let i = 0; i < PAGE_FAIL_MAX; i++) await guardedFetch(`/api/gone/${i}`);
      await expect(guardedFetch('/api/never/seen')).rejects.toBeInstanceOf(RequestLoopBlockedError);
    } finally {
      delete win.__NARRANEXUS_CONFIG__;
    }
  });

  it('a loopback backend with no mode set has no page-wide limits (bash run.sh in a browser)', async () => {
    useRuntimeStore.setState({ mode: null }); // jsdom origin is localhost; no API override
    const spy = vi.spyOn(globalThis, 'fetch').mockImplementation(async () => new Response('', { status: 404 }));
    for (let i = 0; i < PAGE_FAIL_MAX * 2; i++) await guardedFetch(`/api/excluded/p${i}`);
    expect(spy).toHaveBeenCalledTimes(PAGE_FAIL_MAX * 2);
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

  it('a read that answered with a success keeps working while other reads exhaust the failure budget', async () => {
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
    // Under the edge line (20 a minute) including the first failure.
    expect(fetchSpy.mock.calls.length).toBeLessThanOrEqual(EDGE_FAIL_SAFE);
    expect(EDGE_FAIL_SAFE).toBeLessThan(20);
  });

  it('one stray failure does not starve an ordinary panel switch', async () => {
    // A 403 from someone else's public agent, then opening an unvisited agent
    // of the viewer's own: its ~15 first reads must all go out.
    const fetchSpy = vi.spyOn(globalThis, 'fetch').mockImplementation((input) =>
      new Promise((resolve) => setTimeout(() => resolve(new Response('{}', {
        status: String(input).includes('foreign') ? 403 : 200,
      })), 20)),
    );
    vi.spyOn(console, 'error').mockImplementation(() => {});
    await guardedFetch('/api/agents/foreign/awareness');
    await Promise.all(Array.from({ length: 15 }, (_, i) => guardedFetch(`/api/agents/mine/panel${i}`)));
    expect(fetchSpy).toHaveBeenCalledTimes(16);
  });

  it('on a clean page a burst of successful first reads all goes out', async () => {
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
