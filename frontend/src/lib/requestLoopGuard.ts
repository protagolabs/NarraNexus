/**
 * @file_name: requestLoopGuard.ts
 * @author: Bin Liang
 * @date: 2026-10-30
 * @description: Client-side brake for runaway GET loops — the same URL fetched too often in a short window is refused locally instead of hitting the network.
 *
 * Why: a render loop (effect → fetch → setState → new deps → effect …) turns
 * one open panel into a sustained request flood. On 2026-10-30 the Channels
 * section did exactly that (~30 req/s) and the edge flood rule banned the
 * user's IP and suspended the account. Component fixes remove the known loop;
 * this is the backstop for the next one, so a frontend bug degrades to a
 * console error on one panel instead of an account ban.
 *
 * Scope: GETs only, two limits — identical URLs (path + query) repeated in a
 * short window, and a page-wide GET budget per minute. Legitimate traffic
 * repeats a URL at most every couple of seconds (the fastest steady poll is
 * 2s); a loop repeats it several times a second. Writes are never blocked —
 * refusing a user's save is worse than the flood. Calls that bypass
 * `api.request` (raw `fetch`) are not covered, and each tab counts alone.
 */

export const LOOP_WINDOW_MS = 5_000;
/** More than this many identical GETs inside LOOP_WINDOW_MS is treated as a loop. */
export const LOOP_MAX_PER_WINDOW = 15;
/** A refused call settles only after this delay, so a component that retries on
 * failure cannot spin at event-loop speed. */
export const LOOP_BLOCK_DELAY_MS = 1_000;
/** While blocked, only attempts closer together than this extend the block: a
 * loop (~1 attempt/s once slowed by the refusal delay) stays blocked, a poller
 * that got caught once (2s+ interval, so 3s+ between attempts) is released. */
export const LOOP_EXTEND_GAP_MS = 2_000;
/** A URL blocked again within this long of its release is a repeat offender:
 * its block doubles each time (5s, 10s, 20s, 40s, capped at LOOP_MAX_BLOCK_MS),
 * which starves loops that back off on failure but refetch at once on success. */
export const LOOP_REPEAT_WITHIN_MS = 60_000;
export const LOOP_MAX_BLOCK_MS = 60_000;
/** Page-wide GET budget per minute: the backstop for loops spread over many URLs
 * or with a changing query. Measured human peak is 281/min per IP and the edge
 * bans at 562/min, so this page's api.request GETs alone stay under the ban line
 * (writes, raw fetch, SSE and static assets still count at the edge). */
export const PAGE_GET_WINDOW_MS = 60_000;
export const PAGE_GET_MAX_PER_WINDOW = 400;
const REPORT_EVERY_MS = 60_000;
const MAX_TRACKED_KEYS = 500;

type Clock = () => number;

export type LoopVerdict = 'ok' | 'loop' | 'page_budget';

export class RequestLoopGuard {
  readonly #hits = new Map<string, number[]>();
  readonly #blockedUntil = new Map<string, number>();
  readonly #lastAttempt = new Map<string, number>();
  readonly #strikes = new Map<string, { count: number; releasedAt: number }>();
  readonly #pageHits: number[] = [];
  readonly #reportedAt = new Map<string, number>();
  readonly #now: Clock;

  constructor(now: Clock = () => Date.now()) {
    this.#now = now;
  }

  /**
   * Decide whether one GET of `key` may go to the network.
   *
   * Per URL: past LOOP_MAX_PER_WINDOW inside LOOP_WINDOW_MS the key is blocked,
   * and every attempt while blocked that follows the previous one within
   * LOOP_EXTEND_GAP_MS pushes the release out to a full LOOP_WINDOW_MS from now.
   * A serial loop slowed by LOOP_BLOCK_DELAY_MS therefore cannot drain the
   * window and leak a new burst, while a normal poller that was caught once
   * (its attempts are further apart) is released when the window runs out.
   *
   * Per page: at most PAGE_GET_MAX_PER_WINDOW admitted GETs per
   * PAGE_GET_WINDOW_MS across all URLs; refused calls do not count against it,
   * so the page recovers as soon as its real rate drops.
   */
  check(key: string): LoopVerdict {
    const now = this.#now();
    const previous = this.#lastAttempt.get(key);
    this.#lastAttempt.set(key, now);
    const until = this.#blockedUntil.get(key);
    if (until !== undefined) {
      if (now < until) {
        if (previous !== undefined && now - previous < LOOP_EXTEND_GAP_MS) {
          this.#blockedUntil.set(key, Math.max(until, now + LOOP_WINDOW_MS));
        }
        return 'loop';
      }
      this.#blockedUntil.delete(key);
      this.#hits.delete(key);
      const s = this.#strikes.get(key);
      if (s) s.releasedAt = until; // when the block actually ended, not this later attempt
    }

    const cutoff = now - LOOP_WINDOW_MS;
    const hits = (this.#hits.get(key) ?? []).filter((t) => t > cutoff);
    hits.push(now);
    this.#hits.set(key, hits);
    if (this.#hits.size > MAX_TRACKED_KEYS) this.#prune(cutoff);
    if (hits.length > LOOP_MAX_PER_WINDOW) {
      const prev = this.#strikes.get(key);
      const count = prev && now - prev.releasedAt < LOOP_REPEAT_WITHIN_MS ? prev.count + 1 : 0;
      this.#strikes.set(key, { count, releasedAt: Number.POSITIVE_INFINITY });
      this.#blockedUntil.set(key, now + Math.min(LOOP_WINDOW_MS * 2 ** count, LOOP_MAX_BLOCK_MS));
      return 'loop';
    }

    const pageCutoff = now - PAGE_GET_WINDOW_MS;
    while (this.#pageHits.length && this.#pageHits[0] <= pageCutoff) this.#pageHits.shift();
    if (this.#pageHits.length >= PAGE_GET_MAX_PER_WINDOW) return 'page_budget';
    this.#pageHits.push(now);
    return 'ok';
  }

  /** True at most once per key per REPORT_EVERY_MS — keeps the console readable. */
  shouldReport(key: string): boolean {
    const now = this.#now();
    const last = this.#reportedAt.get(key);
    if (last !== undefined && now - last < REPORT_EVERY_MS) return false;
    this.#reportedAt.set(key, now);
    return true;
  }

  #prune(cutoff: number): void {
    const now = this.#now();
    for (const [key, hits] of this.#hits) {
      const until = this.#blockedUntil.get(key);
      const blocked = (until ?? 0) > now;
      if (!blocked && hits.every((t) => t <= cutoff)) {
        const s = this.#strikes.get(key);
        // An expired block that was never followed by an attempt has no release
        // time yet; it ended at `until`.
        if (s && s.releasedAt === Number.POSITIVE_INFINITY && until !== undefined) s.releasedAt = until;
        if (s && now - s.releasedAt >= LOOP_REPEAT_WITHIN_MS) this.#strikes.delete(key);
        this.#hits.delete(key);
        this.#blockedUntil.delete(key);
        this.#lastAttempt.delete(key);
      }
    }
  }
}

export function isGuardedMethod(method: string | undefined): boolean {
  return !method || method.toUpperCase() === 'GET';
}

/** Resolve after `ms`, or reject with the abort reason as soon as `signal` aborts. */
export function delayUnlessAborted(ms: number, signal?: AbortSignal | null): Promise<void> {
  return new Promise((resolve, reject) => {
    if (signal?.aborted) {
      reject(signal.reason ?? new DOMException('Aborted', 'AbortError'));
      return;
    }
    const onAbort = () => {
      clearTimeout(timer);
      reject(signal?.reason ?? new DOMException('Aborted', 'AbortError'));
    };
    const timer = setTimeout(() => {
      signal?.removeEventListener('abort', onAbort);
      resolve();
    }, ms);
    signal?.addEventListener('abort', onAbort, { once: true });
  });
}
