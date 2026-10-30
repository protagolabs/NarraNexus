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
 * The total read budget is off only when the backend is known to be local
 * (`knownLocalBackend`); against it there is no edge ban to avoid. Unknown
 * means on.
 *
 * Tabs share the page budgets over a BroadcastChannel because the edge flood
 * rule counts per IP: two tabs each staying under their own budget could still
 * sum past the ban line. Where BroadcastChannel is unavailable the guard
 * degrades to per-tab budgets.
 */
import { RequestLoopBlockedError } from './apiError';
import { isForcedCloud, isForcedLocal } from './runtimeConfig';
import { isTauri } from './tauri';
import { getApiBaseUrl, useRuntimeStore } from '@/stores/runtimeStore';
import {
  delayUnlessAborted,
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
  const g = new RequestLoopGuard({
    onSpend: (kind) => channel?.postMessage(kind),
    totalBudgetEnabled: () => !knownLocalBackend(),
  });
  if (channel) {
    channel.onmessage = (event: MessageEvent<unknown>) => {
      if (event.data === 'rapid' || event.data === 'total') g.recordRemote(event.data as BudgetKind);
    };
  }
  return g;
}

const LOOPBACK_HOSTS = new Set(['localhost', '127.0.0.1', '[::1]', '::1']);

/**
 * True only when the backend is known to be local — no edge flood ban in front
 * of it, so the total read budget would only refuse real work. Fail-closed:
 * an unknown mode keeps the budget on, because it is the only layer that holds
 * a loop whose query changes on every call.
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
  if (isGuardedMethod(methodOf(input, init))) {
    const url = urlOf(input);
    const verdict = guard.check(url);
    if (verdict !== 'ok') {
      // Budget reports throttle under one fixed key each, so the console gets one
      // line a minute rather than one per refused read.
      const reportKey = verdict === 'loop' ? url : `<${verdict}>`;
      if (guard.shouldReport(reportKey)) {
        console.error(
          verdict === 'loop'
            ? `[request-loop-guard] refusing repeated read of ${url} (a component is fetching it in a loop)`
            : verdict === 'rapid_budget'
              ? `[request-loop-guard] ${guard.spent('rapid')} rapid repeat reads in the last minute; refusing ${url}`
              : `[request-loop-guard] ${guard.spent('total')} reads in the last minute, at the edge ban line; refusing ${url}`,
        );
      }
      await delayUnlessAborted(LOOP_BLOCK_DELAY_MS, init?.signal);
      throw new RequestLoopBlockedError();
    }
  }
  return fetch(input, init);
}

/** Tests only: a fresh guard (and channel) so cases cannot leak budget into each other. */
export function __resetRequestLoopGuardForTests(): void {
  guard = createGuard();
}
