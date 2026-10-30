/**
 * @file_name: guardedFetch.ts
 * @author: Bin Liang
 * @date: 2026-10-30
 * @description: The app's network calls: `fetch` behind the per-page request-loop guard, with its page budgets shared across same-origin tabs.
 *
 * Every HTTP call in the app goes through `guardedFetch` (a source scan,
 * `no-raw-fetch.test.ts`, enforces it; the exceptions are sessionGuard's
 * session probe and WebSocket traffic), so a render loop anywhere — the API
 * client, the artifact / office-watch services, raw blob downloads, the plugin
 * loader — hits the same brake. Reads that the guard refuses wait LOOP_BLOCK_DELAY_MS and throw
 * `RequestLoopBlockedError`; everything else is plain `fetch`, returning the
 * `Response` untouched so callers keep their own status handling.
 *
 * The total read budget applies only when the backend is the cloud deployment
 * (`behindCloudEdge`); against a local backend there is no edge ban to avoid.
 *
 * Tabs share the page budgets over a BroadcastChannel because the edge flood
 * rule counts per IP: two tabs each staying under their own budget could still
 * sum past the ban line. Where BroadcastChannel is unavailable the guard
 * degrades to per-tab budgets.
 */
import { RequestLoopBlockedError } from './apiError';
import { isForcedCloud } from './runtimeConfig';
import { useRuntimeStore } from '@/stores/runtimeStore';
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
    totalBudgetEnabled: behindCloudEdge,
  });
  if (channel) {
    channel.onmessage = (event: MessageEvent<unknown>) => {
      if (event.data === 'rapid' || event.data === 'total') g.recordRemote(event.data as BudgetKind);
    };
  }
  return g;
}

/** The edge flood ban only exists in front of the cloud deployment. */
function behindCloudEdge(): boolean {
  return isForcedCloud() || useRuntimeStore.getState().mode === 'cloud-web';
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
      const reportKey = verdict === 'loop' ? url : '<page-budget>';
      if (guard.shouldReport(reportKey)) {
        console.error(
          verdict === 'loop'
            ? `[request-loop-guard] refusing repeated read of ${url} — a component is fetching it in a loop`
            : `[request-loop-guard] page read budget spent (loop-shaped traffic), refusing ${url}`,
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
