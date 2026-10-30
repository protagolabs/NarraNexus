/**
 * @file_name: guardedFetch.ts
 * @author: Bin Liang
 * @date: 2026-10-30
 * @description: The app's network calls: `fetch` behind the per-page request-loop guard, with its page budgets shared across same-origin tabs.
 *
 * Every HTTP call in the app's own source goes through `guardedFetch` (a source
 * scan, `no-raw-fetch.test.ts`, enforces it; the exceptions are sessionGuard's
 * session probe and WebSocket traffic), so a render loop anywhere in it — the
 * API client, the artifact / office-watch services, raw blob downloads, the
 * plugin loader — hits the same brake. NOT covered: plugin bundles. They run as
 * modules in this page's realm and can call the global `fetch` directly;
 * `HostAPI.http` (platform/host.ts) is guarded, but nothing forces a plugin to
 * use it. Reads that the guard refuses wait LOOP_BLOCK_DELAY_MS and throw
 * `RequestLoopBlockedError`; everything else is plain `fetch`, returning the
 * `Response` untouched so callers keep their own status handling.
 *
 * A guarded read that comes back with a probing-like failure status (400 /
 * 403 / 404 / 405 — what the edge monitor bans on) is reported to the guard,
 * which blocks a URL that keeps failing and, once the page (all tabs together)
 * has failed PAGE_FAIL_MAX reads in a minute, pauses reads of paths not known
 * to work; a loop on a deleted agent's 404 stays far below the edge's failure
 * line, while paths that answered 2xx (agent list, chat) keep working.
 *
 * Tabs share the page budgets over a BroadcastChannel because the edge's
 * failure rule counts per IP: two tabs each staying under their own budget
 * could still sum past the ban line. Where BroadcastChannel is unavailable the guard
 * degrades to per-tab budgets.
 */
import { RequestLoopBlockedError } from './apiError';
import {
  delayUnlessAborted,
  FAILURE_STATUSES,
  isGuardedMethod,
  LOOP_BLOCK_DELAY_MS,
  RequestLoopGuard,
  type BudgetKind,
} from './requestLoopGuard';

const CHANNEL_NAME = 'narranexus.request-loop-guard';

let channel: BroadcastChannel | null = null;
let guard = createGuard();

function createGuard(): RequestLoopGuard {
  channel?.close();
  channel = null;
  if (typeof BroadcastChannel !== 'undefined') {
    try {
      channel = new BroadcastChannel(CHANNEL_NAME);
    } catch {
      channel = null; // e.g. an opaque origin: per-tab budgets only
    }
  }
  const g = new RequestLoopGuard({ onSpend: (kind) => channel?.postMessage(kind) });
  if (channel) {
    channel.onmessage = (event: MessageEvent<unknown>) => {
      if (event.data === 'rapid' || event.data === 'fail') g.recordRemote(event.data as BudgetKind);
    };
  }
  return g;
}

function urlOf(input: RequestInfo | URL): string {
  if (typeof input === 'string') return input;
  if (input instanceof URL) return input.href;
  return input.url;
}

function methodOf(input: RequestInfo | URL, init?: RequestInit): string | undefined {
  if (init?.method) return init.method;
  return typeof input === 'object' && 'method' in input ? input.method : undefined;
}

export async function guardedFetch(input: RequestInfo | URL, init?: RequestInit): Promise<Response> {
  if (isGuardedMethod(methodOf(input, init))) {
    const url = urlOf(input);
    const verdict = guard.check(url);
    if (verdict !== 'ok') {
      // Budget reports throttle under one fixed key each, so the console gets one
      // line a minute rather than one per refused read.
      const reportKey = verdict === 'rapid_budget' || verdict === 'fail_budget' ? `<${verdict}>` : url;
      if (guard.shouldReport(reportKey)) {
        console.error(
          verdict === 'loop'
            ? `[request-loop-guard] refusing repeated read of ${url} (a component is fetching it in a loop)`
            : verdict === 'failing'
              ? `[request-loop-guard] refusing ${url} for a minute: it keeps failing`
              : verdict === 'fail_budget'
                ? `[request-loop-guard] ${guard.spent('fail')} failed reads in the last minute; pausing reads (refusing ${url})`
                : `[request-loop-guard] ${guard.spent('rapid')} rapid repeat reads in the last minute; refusing ${url}`,
        );
      }
      await delayUnlessAborted(LOOP_BLOCK_DELAY_MS, init?.signal);
      throw new RequestLoopBlockedError();
    }
  }
  if (!isGuardedMethod(methodOf(input, init))) return fetch(input, init);
  const url = urlOf(input);
  const settle = guard.track(url);
  try {
    const response = await fetch(input, init);
    if (FAILURE_STATUSES.has(response.status)) guard.recordFailure(url);
    else if (response.ok) guard.recordSuccess(url);
    return response;
  } finally {
    settle();
  }
}

/** Tests only: a fresh guard (and channel) so cases cannot leak budget into each other. */
export function __resetRequestLoopGuardForTests(): void {
  guard = createGuard();
}
