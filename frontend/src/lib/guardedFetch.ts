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
 * use it. Reads the guard refuses throw `RequestLoopBlockedError` — after
 * LOOP_BLOCK_DELAY_MS for the per-URL verdicts (a retry loop on one URL is
 * slowed), at once for page-budget verdicts (retrying sooner cannot change a
 * page-wide state; the delay would only stall callers that walk a list).
 * Everything else is plain `fetch`, returning the `Response` untouched so
 * callers keep their own status handling.
 *
 * A guarded read that comes back with a probing-like failure status (400 /
 * 403 / 404 / 405 — what the edge monitor bans on) is reported to the guard,
 * which blocks a URL that keeps failing and, once the page (all tabs together)
 * has failed PAGE_FAIL_MAX reads in a minute, pauses reads of paths not known
 * to work; a loop on a deleted agent's 404 stays far below the edge's failure
 * line, while paths that answered 2xx (agent list, chat) keep working. Reads of
 * unproven paths wait for one of the guard's in-flight slots (`acquire`), so a
 * burst of them reaches the server at most EDGE_FAIL_SAFE at a time.
 *
 * The page-wide limits are switched off only when the backend is known to be
 * local (`knownLocalBackend`: no edge ban in front of it, so they could only
 * refuse real work — e.g. a minimal distribution whose excluded plugins'
 * routes 404 on every poll). Unknown means on.
 *
 * Tabs share the page budgets over a BroadcastChannel because the edge's
 * failure rule counts per IP: two tabs each staying under their own budget
 * could still sum past the ban line. Where BroadcastChannel is unavailable the guard
 * degrades to per-tab budgets.
 */
import { RequestLoopBlockedError } from './apiError';
import { isForcedCloud, isForcedLocal } from './runtimeConfig';
import { isTauri } from './tauri';
import { getApiBaseUrl, useRuntimeStore } from '@/stores/runtimeStore';
import {
  delayUnlessAborted,
  FAILURE_STATUSES,
  isGuardedMethod,
  LOOP_BLOCK_DELAY_MS,
  RequestLoopGuard,
  type BudgetKind,
  type LoopVerdict,
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
  const g = new RequestLoopGuard({
    onSpend: (kind) => channel?.postMessage(kind),
    edgeLimitsEnabled: () => !knownLocalBackend(),
  });
  if (channel) {
    channel.onmessage = (event: MessageEvent<unknown>) => {
      if (event.data === 'rapid' || event.data === 'fail') g.recordRemote(event.data as BudgetKind);
    };
  }
  return g;
}

const LOOPBACK_HOSTS = new Set(['localhost', '127.0.0.1', '[::1]', '::1']);

/**
 * True only when the backend is known to be local — no edge ban in front of
 * it. Fail-closed: an unknown mode keeps the page-wide limits on (a LAN
 * self-host on a non-loopback address counts as "not known local").
 */
function knownLocalBackend(): boolean {
  const mode = useRuntimeStore.getState().mode;
  if (isForcedCloud() || mode === 'cloud-web') return false;
  if (isTauri() || isForcedLocal() || mode === 'local') return true;
  try {
    const base = getApiBaseUrl() || window.location.origin;
    return LOOPBACK_HOSTS.has(new URL(base, window.location.href).hostname);
  } catch {
    return false;
  }
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
  if (!isGuardedMethod(methodOf(input, init))) return fetch(input, init);
  const url = urlOf(input);
  const verdict = guard.check(url);
  if (verdict !== 'ok') await refuse(verdict, url, init, input);
  const release = await guard.acquire(url, signalOf(input, init));
  if (!release) await refuse('fail_budget', url, init, input);
  try {
    const response = await fetch(input, init);
    if (FAILURE_STATUSES.has(response.status)) guard.recordFailure(url);
    else if (response.status < 400) guard.recordSuccess(url);
    return response;
  } finally {
    release?.();
  }
}

function signalOf(input: RequestInfo | URL, init?: RequestInit): AbortSignal | undefined {
  return init?.signal ?? (input instanceof Request ? input.signal : undefined);
}

async function refuse(verdict: LoopVerdict, url: string, init: RequestInit | undefined, input: RequestInfo | URL): Promise<never> {
  // Budget reports throttle under one fixed key each, so the console gets one
  // line a minute rather than one per refused read.
  const pageWide = verdict === 'rapid_budget' || verdict === 'fail_budget';
  const reportKey = pageWide ? `<${verdict}>` : url;
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
  // Per-URL verdicts slow a retry loop on that URL; a page-wide state does not
  // change by retrying sooner, so those refusals are immediate.
  if (!pageWide) await delayUnlessAborted(LOOP_BLOCK_DELAY_MS, signalOf(input, init));
  throw new RequestLoopBlockedError();
}

/** Tests only: a fresh guard (and channel) so cases cannot leak budget into each other. */
export function __resetRequestLoopGuardForTests(): void {
  guard = createGuard();
}
