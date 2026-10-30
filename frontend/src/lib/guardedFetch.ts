/**
 * @file_name: guardedFetch.ts
 * @author: Bin Liang
 * @date: 2026-10-30
 * @description: The app's network calls: `fetch` behind the per-page request-loop guard, with its page budgets shared across same-origin tabs.
 *
 * Every HTTP call in the app's own source goes through `guardedFetch` (a source
 * scan, `no-raw-fetch.test.ts`, enforces it), so a render loop anywhere in it —
 * the API client, the artifact / office-watch services, raw blob downloads, the
 * plugin loader — hits the same brake. Plugin bundles run as modules in this
 * page's realm and are invisible to that scan; their calls to the main-realm
 * global `fetch` are covered by `installGlobalFetchGuard` (own-backend requests
 * only). The one unguarded path is `rawFetch`, allowed only for sessionGuard's
 * session probe. NOT covered: WebSocket traffic, and a plugin's
 * XMLHttpRequest (e.g. axios), Worker or iframe fetches — none of the in-tree
 * plugins use them. Reads the guard refuses throw `RequestLoopBlockedError` — after
 * LOOP_BLOCK_DELAY_MS for the per-URL verdicts and 'slot_full' (a retry would
 * be refused again at once; the delay slows that loop), at once for the
 * page-budget verdicts and 'slot_wait' (retrying sooner cannot change a
 * page-wide state, and a slot_wait retry queues again for up to 15s).
 * Everything else is plain `fetch`, returning the `Response` untouched so
 * callers keep their own status handling.
 *
 * A guarded read that comes back with a probing-like failure status (400 /
 * 403 / 404 / 405 — what the edge monitor bans on) is reported to the guard,
 * which blocks a URL that keeps failing and, once the page (all tabs together)
 * has failed PAGE_FAIL_MAX reads in a minute, pauses reads of paths not known
 * to work; a loop on a deleted agent's 404 stays far below the edge's failure
 * line, while paths whose last answer was a success (agent list, chat) keep
 * working. Reads of
 * unproven paths wait for one of the guard's in-flight slots (`acquire`), so in
 * one tab a burst of them reaches the server at most EDGE_FAIL_SAFE at a time
 * (slots are per tab; only landed failures are shared). A read that waits
 * longer than SLOT_WAIT_MAX_MS is refused.
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
  EDGE_FAIL_SAFE,
  SLOT_WAIT_MAX_MS,
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
    onSlotWait: () => {
      if (g.shouldReport('<slot_queue>')) {
        console.warn(`[request-loop-guard] reads queueing for ${EDGE_FAIL_SAFE} in-flight slots`);
      }
    },
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
 * it. Fail-closed: an unknown mode keeps the page-wide limits on. Note the app
 * resolves the mode to 'local' for every build that is not forced to cloud
 * (App.tsx useResolveAppMode), so in practice the deciding input is the forced
 * cloud flag, which the cloud deployment sets (NARRANEXUS_FORCE_MODE=cloud).
 */
function knownLocalBackend(): boolean {
  const local = detectLocalBackend();
  // One line per page load, so a deployment that has an edge in front but
  // forgot the forced-cloud flag is visible at a glance.
  if (local && !reportedLocal) {
    reportedLocal = true;
    console.info('[request-loop-guard] edge limits off (local backend)');
  }
  return local;
}

let reportedLocal = false;

function detectLocalBackend(): boolean {
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
  if (input instanceof Request) return input.url;
  // A URL (also one from another realm) or any object with a toString.
  return String(input);
}

function methodOf(input: RequestInfo | URL, init?: RequestInit): string | undefined {
  if (init?.method) return init.method;
  return typeof input === 'object' && 'method' in input ? input.method : undefined;
}

/**
 * The browser's own fetch, captured when the global guard is installed. Kept
 * on globalThis under a registry symbol rather than in module scope, so a
 * re-evaluated copy of this module (dev HMR) still calls the real fetch
 * instead of the previous copy's wrapper (which would guard every read twice).
 */
const NATIVE_FETCH = Symbol.for('narranexus.nativeFetch');
type NativeSlot = { [NATIVE_FETCH]?: { original: typeof fetch; bound: typeof fetch } };
const nativeSlot = globalThis as unknown as NativeSlot;

/**
 * The unguarded network call. Only this file and the session probe
 * (lib/sessionGuard.ts) may use it — a source scan enforces that. Before the
 * global guard is installed it is simply the current global fetch.
 */
export function rawFetch(input: RequestInfo | URL, init?: RequestInit): Promise<Response> {
  return (nativeSlot[NATIVE_FETCH]?.bound ?? globalThis.fetch)(input, init);
}

/** Whether a request goes to this app's own origin or its API backend — the
 * only traffic the edge monitor sees, so the only traffic worth guarding. */
function isOwnBackend(input: RequestInfo | URL): boolean {
  try {
    const target = new URL(urlOf(input), window.location.href).origin;
    if (target === window.location.origin) return true;
    const base = getApiBaseUrl();
    return Boolean(base) && target === new URL(base, window.location.href).origin;
  } catch {
    return false;
  }
}

/**
 * Route the page's global `fetch` through the guard, so code that calls it
 * directly — plugin bundles, which run as modules in this realm and are not
 * covered by the source scan — hits the same brake. Requests to other origins
 * pass straight through: the edge never sees them. Call once, before any
 * plugin loads; repeat calls do nothing.
 */
export function installGlobalFetchGuard(): void {
  if (nativeSlot[NATIVE_FETCH]) return;
  const original = globalThis.fetch;
  nativeSlot[NATIVE_FETCH] = { original, bound: original.bind(globalThis) };
  globalThis.fetch = (input: RequestInfo | URL, init?: RequestInit) =>
    isOwnBackend(input) ? guardedFetch(input, init) : rawFetch(input, init);
}

export async function guardedFetch(input: RequestInfo | URL, init?: RequestInit): Promise<Response> {
  if (!isGuardedMethod(methodOf(input, init))) return rawFetch(input, init);
  const url = urlOf(input);
  const verdict = guard.check(url);
  if (verdict !== 'ok') await refuse(verdict, url, init, input);
  const release = await guard.acquire(url, signalOf(input, init));
  if (typeof release !== 'function') await refuse(release, url, init, input);
  try {
    const response = await rawFetch(input, init);
    if (FAILURE_STATUSES.has(response.status)) guard.recordFailure(url);
    else if (response.status >= 200 && response.status < 400) guard.recordSuccess(url);
    return response;
  } finally {
    if (typeof release === 'function') release();
  }
}

function signalOf(input: RequestInfo | URL, init?: RequestInit): AbortSignal | undefined {
  return init?.signal ?? (input instanceof Request ? input.signal : undefined);
}

async function refuse(verdict: LoopVerdict, url: string, init: RequestInit | undefined, input: RequestInfo | URL): Promise<never> {
  // Budget reports throttle under one fixed key each, so the console gets one
  // line a minute rather than one per refused read.
  const pageWide = verdict === 'rapid_budget' || verdict === 'fail_budget' || verdict === 'slot_wait';
  const reportKey = pageWide || verdict === 'slot_full' ? `<${verdict}>` : url;
  if (guard.shouldReport(reportKey)) {
    console.error(
      verdict === 'loop'
        ? `[request-loop-guard] refusing repeated read of ${url} (fetch loop)`
        : verdict === 'failing'
          ? `[request-loop-guard] refusing ${url} for a minute: it keeps failing`
          : verdict === 'fail_budget'
            ? `[request-loop-guard] ${guard.spent('fail')} failed reads/min; pausing reads (refusing ${url})`
            : verdict === 'slot_wait'
              ? `[request-loop-guard] no slot freed in ${SLOT_WAIT_MAX_MS / 1000}s (slow backend?); refusing ${url}`
              : verdict === 'slot_full'
                ? `[request-loop-guard] slot queue full; refusing ${url}`
                : `[request-loop-guard] ${guard.spent('rapid')} rapid repeat reads/min; refusing ${url}`,
    );
  }
  // Per-URL verdicts slow a retry loop on that URL; a page-wide state does not
  // change by retrying sooner, so those refusals are immediate. ('slot_wait'
  // needs no delay either: a retry queues again and waits up to
  // SLOT_WAIT_MAX_MS. 'slot_full' does: the queue is full, so a retry would
  // be refused again at once — the delay is what slows that loop.)
  //
  // A read refused here has already passed check(), so it counted toward its
  // URL's window and, if a rapid repeat, the rapid budget — accepted: refusals
  // are rare and only make the guard stricter.
  if (!pageWide) await delayUnlessAborted(LOOP_BLOCK_DELAY_MS, signalOf(input, init));
  throw new RequestLoopBlockedError(verdict === 'slot_wait' ? 'slow' : 'limit');
}

/** Tests only: undo installGlobalFetchGuard, restoring the original fetch. */
export function __uninstallGlobalFetchGuardForTests(): void {
  const saved = nativeSlot[NATIVE_FETCH];
  if (saved) globalThis.fetch = saved.original;
  delete nativeSlot[NATIVE_FETCH];
}

/** Tests only: a fresh guard (and channel) so cases cannot leak budget into each other. */

export function __resetRequestLoopGuardForTests(): void {
  reportedLocal = false;
  guard.dispose();
  guard = createGuard();
}
