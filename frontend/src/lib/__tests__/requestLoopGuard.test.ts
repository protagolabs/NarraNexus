/**
 * @file_name: requestLoopGuard.test.ts
 * @author: Bin Liang
 * @date: 2026-10-30
 * @description: The request-loop guard refuses an identical GET repeated past the window limit, keeps a spinning loop at zero network traffic until it goes quiet for a full window, caps the page-wide GET rate, and never touches writes.
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
  PAGE_TOTAL_MAX,
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

  it('caps a changing-query loop with the total budget, and recovers afterwards', () => {
    // Never repeats a URL, so the per-URL layer cannot see it.
    let now = 0;
    const g = new RequestLoopGuard(() => now);
    let admitted = 0;
    for (let i = 0; i < 2_000; i++) {
      if (g.check(`/api/x?t=${i}`) === 'ok') admitted += 1;
      now += 10; // 100 distinct reads/s for 20s
    }
    expect(admitted).toBe(PAGE_TOTAL_MAX);
    now += PAGE_WINDOW_MS;
    expect(g.check('/api/after')).toBe('ok');
  });

  it('caps a loop over a fixed set of URLs, each read every 2s+, with the total budget (pre-push review I-A)', () => {
    // 20 agents fetched in turn at 10 reads/s: every URL is hit only every 2s,
    // so neither the per-URL layer nor the rapid budget sees it.
    let now = 0;
    const g = new RequestLoopGuard(() => now);
    let admitted = 0;
    for (let i = 0; i < 600; i++) {
      if (g.check(`/api/agents/agent_${i % 20}/simple-chat-history`) === 'ok') admitted += 1;
      now += 100;
    }
    expect(admitted).toBeLessThanOrEqual(PAGE_TOTAL_MAX);
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

  it('ordinary polling is never refused while it stays under the edge ban line (review I-2)', () => {
    // 100 agents' chat history every 15s plus the other background polls:
    // ~440 reads/min, under PAGE_TOTAL_MAX, and it never spends the rapid budget.
    let now = 0;
    const g = new RequestLoopGuard(() => now);
    const refused: string[] = [];
    for (let t = 0; t < 5 * 60_000; t += 1_000) {
      now = t;
      const due: string[] = [];
      if (t % 15_000 === 0) for (let a = 0; a < 100; a++) due.push(`/api/agents/agent_${a}/simple-chat-history?limit=5`);
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

  it('the total budget can be switched off (local backend: no edge ban to avoid)', () => {
    let now = 0;
    const g = new RequestLoopGuard({ now: () => now, totalBudgetEnabled: () => false });
    let admitted = 0;
    for (let i = 0; i < 2_000; i++) {
      if (g.check(`/api/x?t=${i}`) === 'ok') admitted += 1;
      now += 10;
    }
    expect(admitted).toBe(2_000);
  });

  it("another tab's messages can at most spend a budget, never pile up beyond it", () => {
    let now = 0;
    const g = new RequestLoopGuard(() => now);
    for (let i = 0; i < 10 * PAGE_TOTAL_MAX; i++) g.recordRemote('total');
    expect(g.spent('total')).toBe(PAGE_TOTAL_MAX);
    now += PAGE_WINDOW_MS + 1;
    expect(g.check('/api/after-burst')).toBe('ok'); // a minute later, not ten
  });

  it('tabs share the page budgets (review I-3)', () => {
    let now = 0;
    const tabs: RequestLoopGuard[] = [];
    for (let i = 0; i < 2; i++) {
      tabs.push(new RequestLoopGuard({
        now: () => now,
        onSpend: (kind) => tabs.filter((_, j) => j !== i).forEach((t) => t.recordRemote(kind)),
      }));
    }
    let admitted = 0;
    for (let i = 0; i < 1_000; i++) {
      if (tabs[i % 2].check(`/api/x?t=${i}`) === 'ok') admitted += 1; // both tabs run the same loop
      now += 10;
    }
    expect(admitted).toBe(PAGE_TOTAL_MAX); // one shared budget, not one per tab
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
    useRuntimeStore.setState({ mode: 'cloud-web' }); // behind the edge: total budget on
    other = new BroadcastChannel(CHANNEL);
  });
  afterEach(() => {
    other.close();
    useRuntimeStore.setState({ mode: null });
    vi.useRealTimers();
    vi.restoreAllMocks();
  });

  it("another tab's reads spend this tab's budget", async () => {
    vi.spyOn(globalThis, 'fetch').mockImplementation(async () => new Response('{}'));
    const report = vi.spyOn(console, 'error').mockImplementation(() => {});
    for (let i = 0; i < PAGE_TOTAL_MAX; i++) other.postMessage('total');
    await new Promise((r) => setTimeout(r, 50)); // let the messages land
    vi.useFakeTimers();
    const refused = guardedFetch('/api/one-more');
    const settled = expect(refused).rejects.toBeInstanceOf(RequestLoopBlockedError);
    await vi.advanceTimersByTimeAsync(LOOP_BLOCK_DELAY_MS);
    await settled;
    // The report states the count and the edge line, not a guessed cause.
    expect(String(report.mock.calls[0][0])).toContain(`${PAGE_TOTAL_MAX} reads in the last minute`);
  });

  it('an unknown mode keeps the total budget on (fail-closed, pre-merge review I-B)', async () => {
    useRuntimeStore.setState({ mode: null });
    const win = window as unknown as { __NARRANEXUS_CONFIG__?: unknown };
    win.__NARRANEXUS_CONFIG__ = { apiUrl: 'https://agent.example.com' }; // mode left unset
    try {
      vi.spyOn(globalThis, 'fetch').mockImplementation(async () => new Response('{}'));
      vi.spyOn(console, 'error').mockImplementation(() => {});
      for (let i = 0; i < PAGE_TOTAL_MAX; i++) other.postMessage('total');
      await new Promise((r) => setTimeout(r, 50));
      vi.useFakeTimers();
      const refused = guardedFetch('/api/one-more');
      const settled = expect(refused).rejects.toBeInstanceOf(RequestLoopBlockedError);
      await vi.advanceTimersByTimeAsync(LOOP_BLOCK_DELAY_MS);
      await settled;
    } finally {
      delete win.__NARRANEXUS_CONFIG__;
    }
  });

  it('a loopback backend with no mode set ignores the total budget (bash run.sh in a browser)', async () => {
    useRuntimeStore.setState({ mode: null }); // jsdom origin is localhost; no API override
    vi.spyOn(globalThis, 'fetch').mockImplementation(async () => new Response('{}'));
    for (let i = 0; i < PAGE_TOTAL_MAX; i++) other.postMessage('total');
    await new Promise((r) => setTimeout(r, 50));
    await expect(guardedFetch('/api/one-more')).resolves.toBeInstanceOf(Response);
  });

  it('a local backend ignores the total budget (no edge in front of it)', async () => {
    useRuntimeStore.setState({ mode: 'local' });
    vi.spyOn(globalThis, 'fetch').mockImplementation(async () => new Response('{}'));
    for (let i = 0; i < PAGE_TOTAL_MAX; i++) other.postMessage('total');
    await new Promise((r) => setTimeout(r, 50));
    await expect(guardedFetch('/api/one-more')).resolves.toBeInstanceOf(Response);
  });

  it("this tab's reads are announced to the other tabs", async () => {
    vi.spyOn(globalThis, 'fetch').mockImplementation(async () => new Response('{}'));
    const seen: unknown[] = [];
    other.onmessage = (e: MessageEvent<unknown>) => seen.push(e.data);
    await guardedFetch('/api/announced');
    await new Promise((r) => setTimeout(r, 50));
    expect(seen).toContain('total');
  });
});
