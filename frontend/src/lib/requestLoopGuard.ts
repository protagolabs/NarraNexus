/**
 * @file_name: requestLoopGuard.ts
 * @author: Bin Liang
 * @date: 2026-10-30
 * @description: Client-side brake for runaway read loops — a URL fetched too often, a URL that keeps failing, or a burst of rapid repeats is refused locally instead of hitting the network.
 *
 * Why: a render loop (effect → fetch → setState → new deps → effect …) turns
 * one open panel into a sustained request flood. On 2026-10-30 the Channels
 * section did exactly that (~30 req/s) and the edge flood rule banned the
 * user's IP and suspended the account. Component fixes remove the known loop;
 * this is the backstop for the next one, so a frontend bug degrades to an
 * error on one panel instead of an account ban.
 *
 * Scope: reads (GET / HEAD) only — refusing a user's save is worse than the
 * flood. Three layers:
 *  - per URL (path + query): an identical URL repeated faster than any
 *    legitimate poll is blocked;
 *  - failures: a read that keeps failing (FAILURE_STATUSES, reported by
 *    guardedFetch through `recordFailure`) blocks its URL for a minute, and the
 *    page as a whole may fail at most PAGE_FAIL_MAX reads a minute (shared by
 *    tabs). Past that, reads of paths not known to work (last answer a failure,
 *    or never answered 2xx) are refused until the window drains; paths whose
 *    last answer was a success (`recordSuccess`) keep working, so a burst of
 *    403s from one panel cannot freeze the agent list or the chat. Once any
 *    read has failed, landed failures plus unproven reads still in flight
 *    (`track`) may not reach EDGE_FAIL_SAFE (18), so a burst of first reads
 *    cannot carry the page past the edge line, while one stray failure leaves
 *    room for an ordinary panel switch. The edge monitor (sentinel,
 *    monitoring v2) bans an IP at 20 probing-like failures a minute
 *    (400/403/404/405), so a looping client must stay below that line on its
 *    own. Limit: a path whose answers alternate between 2xx and failure stays
 *    "proven" and is held only by the per-URL failure block;
 *  - a page-wide "rapid" budget (the same URL again within LOOP_EXTEND_GAP_MS)
 *    shared by same-origin tabs (`recordRemote`, wired in guardedFetch.ts),
 *    which stops loops spread over a few URLs, each under the per-URL limit.
 * Request VOLUME alone is not judged: monitoring v2 at the edge no longer bans
 * on it (this guard must ship together with or after that edge change), and
 * ordinary polling must never be refused however many agents it covers.
 * The app's network calls go through `guardedFetch` (enforced by a source
 * scan), which owns the single instance.
 */

export const LOOP_WINDOW_MS = 5_000;
/** More than this many identical reads inside LOOP_WINDOW_MS is treated as a loop. */
export const LOOP_MAX_PER_WINDOW = 15;
/** A refused call settles only after this delay, so a component that retries on
 * failure cannot spin at event-loop speed. */
export const LOOP_BLOCK_DELAY_MS = 1_000;
/** Attempts closer together than this are "rapid": while a URL is blocked only
 * rapid attempts extend the block (a loop slowed to ~1 attempt/s stays blocked,
 * a 2s+ poller caught once is released), and rapid repeats spend the page's
 * rapid budget. */
export const LOOP_EXTEND_GAP_MS = 2_000;
/** A URL blocked again within this long of its release is a repeat offender:
 * its block doubles each time (5s, 10s, 20s, 40s, capped at LOOP_MAX_BLOCK_MS),
 * which starves loops that back off on failure but refetch at once on success. */
export const LOOP_REPEAT_WITHIN_MS = 60_000;
export const LOOP_MAX_BLOCK_MS = 60_000;
/** Statuses the edge monitor counts as probing-like failures (and so must a
 * looping client not produce in bulk). 401 / 429 / 5xx are deliberately absent. */
export const FAILURE_STATUSES: ReadonlySet<number> = new Set([400, 403, 404, 405]);
/** FAIL_MAX failures of one URL within FAIL_WINDOW_MS block it for FAIL_BLOCK_MS:
 * a failing loop leaks at most FAIL_MAX failures per URL per ~70s, well under the
 * edge's 20 failures a minute per IP even across several URLs. */
export const FAIL_WINDOW_MS = 10_000;
export const FAIL_MAX = 3;
export const FAIL_BLOCK_MS = 60_000;
/** Page-wide budgets over PAGE_WINDOW_MS, shared across same-origin tabs: rapid
 * repeats, and failed reads (real users peak at 6 a minute; the edge bans at 20). */
export const PAGE_WINDOW_MS = 60_000;
export const PAGE_RAPID_MAX = 150;
export const PAGE_FAIL_MAX = 10;
/** Failures landed plus unproven reads in flight may not reach this: the edge
 * bans at 20 a minute. Separate from PAGE_FAIL_MAX so one stray failure does not
 * starve the dozen first reads of an ordinary panel switch. */
export const EDGE_FAIL_SAFE = 18;
const REPORT_EVERY_MS = 60_000;
const MAX_TRACKED_KEYS = 500;
const MAX_TRACKED_PATHS = 1000;

/**
 * The endpoint a guard key (a URL, absolute or relative) addresses: its path,
 * plus the `agent_id` query parameter when present — several endpoints take the
 * agent there (`/api/jobs?agent_id=…`), and a 403 on someone else's agent must
 * not mark the viewer's own agent's reads as failing. Origin, other query
 * parameters and the fragment are dropped, so a loop that changes its query on
 * every call is still one path, and relative and absolute spellings agree.
 */
function pathOf(key: string): string {
  let url: URL;
  try {
    url = new URL(key, 'http://guard.invalid');
  } catch {
    return key.split(/[?#]/, 1)[0];
  }
  const agent = url.searchParams.get('agent_id');
  return agent === null ? url.pathname : `${url.pathname}?agent_id=${agent}`;
}

type Clock = () => number;
const NOOP = (): void => {};

export type LoopVerdict = 'ok' | 'loop' | 'failing' | 'rapid_budget' | 'fail_budget';
export type BudgetKind = 'rapid' | 'fail';

export interface RequestLoopGuardOptions {
  now?: Clock;
  /** Called when an admitted request spends a page budget (to tell other tabs). */
  onSpend?: (kind: BudgetKind) => void;
}

export class RequestLoopGuard {
  readonly #hits = new Map<string, number[]>();
  readonly #blockedUntil = new Map<string, number>();
  readonly #lastAttempt = new Map<string, number>();
  readonly #strikes = new Map<string, { count: number; releasedAt: number }>();
  readonly #failures = new Map<string, number[]>();
  readonly #failBlockedUntil = new Map<string, number>();
  /** Last outcome per path (true = 2xx, false = failure status); insertion-ordered, capped. */
  readonly #pathOk = new Map<string, boolean>();
  #inflightUnproven = 0;
  readonly #budget: Record<BudgetKind, number[]> = { rapid: [], fail: [] };
  readonly #reportedAt = new Map<string, number>();
  readonly #now: Clock;
  readonly #onSpend?: (kind: BudgetKind) => void;

  constructor(options: RequestLoopGuardOptions | Clock = {}) {
    const opts = typeof options === 'function' ? { now: options } : options;
    this.#now = opts.now ?? (() => Date.now());
    this.#onSpend = opts.onSpend;
  }

  /**
   * Decide whether one read of `key` may go to the network.
   *
   * Per URL: past LOOP_MAX_PER_WINDOW inside LOOP_WINDOW_MS the key is blocked
   * (for longer if it is a repeat offender), and while blocked every rapid
   * attempt pushes the release out to a full LOOP_WINDOW_MS from now.
   *
   * Failing: a URL that `recordFailure` saw fail FAIL_MAX times inside
   * FAIL_WINDOW_MS is refused until FAIL_BLOCK_MS after the last of them.
   * While the page's failure budget is spent (PAGE_FAIL_MAX failures landed),
   * or once any read has failed and landed failures plus unproven reads still
   * in flight (`track`) reach EDGE_FAIL_SAFE, a read is refused unless the last
   * answer for its path (see `pathOf`: agent_id kept, other query parameters
   * dropped) was a success.
   *
   * Page: a rapid repeat spends the rapid budget; when it is spent the request
   * is refused. A request refused by the budget spends nothing and does not
   * count toward the URL's window; its attempt time is still recorded, so a
   * loop that keeps retrying keeps reading as rapid instead of slipping out
   * every LOOP_EXTEND_GAP_MS.
   */
  check(key: string): LoopVerdict {
    const now = this.#now();
    const previous = this.#lastAttempt.get(key);
    this.#lastAttempt.set(key, now);
    // Bound the bookkeeping on every call, whatever the verdict: a loop that
    // keeps getting refused is exactly when the maps grow.
    if (this.#lastAttempt.size > MAX_TRACKED_KEYS) this.#prune(now, key);

    const failUntil = this.#failBlockedUntil.get(key);
    if (failUntil !== undefined) {
      if (now < failUntil) return 'failing';
      this.#failBlockedUntil.delete(key);
      this.#failures.delete(key);
    }
    const failed = this.#expireBudget('fail', now).length;
    if (
      this.#pathOk.get(pathOf(key)) !== true
      && (failed >= PAGE_FAIL_MAX || (failed > 0 && failed + this.#inflightUnproven >= EDGE_FAIL_SAFE))
    ) {
      return 'fail_budget';
    }

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
    if (hits.length > LOOP_MAX_PER_WINDOW) {
      this.#hits.set(key, hits);
      const prev = this.#strikes.get(key);
      const count = prev && now - prev.releasedAt < LOOP_REPEAT_WITHIN_MS ? prev.count + 1 : 0;
      this.#strikes.set(key, { count, releasedAt: Number.POSITIVE_INFINITY });
      this.#blockedUntil.set(key, now + Math.min(LOOP_WINDOW_MS * 2 ** count, LOOP_MAX_BLOCK_MS));
      return 'loop';
    }

    const rapid = previous !== undefined && now - previous < LOOP_EXTEND_GAP_MS;
    const rapidSpent = rapid ? this.#expireBudget('rapid', now) : null;
    if (rapidSpent && rapidSpent.length >= PAGE_RAPID_MAX) return 'rapid_budget';
    if (rapidSpent) {
      rapidSpent.push(now);
      this.#onSpend?.('rapid');
    }
    this.#hits.set(key, hits);
    return 'ok';
  }

  /**
   * An admitted read of `key` is going out; call the returned function once it
   * settles (success, failure, network error or abort alike). While any read
   * has failed in the window, a read of a path not known to work counts as a
   * pending failure until then, so concurrent first reads cannot overshoot the
   * page failure budget. On a clean page (no failures) nothing is counted, so
   * the first load's burst of reads is never held back.
   */
  track(key: string): () => void {
    if (this.#pathOk.get(pathOf(key)) === true) return NOOP;
    if (this.#expireBudget('fail', this.#now()).length === 0) return NOOP;
    this.#inflightUnproven += 1;
    let settled = false;
    return () => {
      if (settled) return;
      settled = true;
      this.#inflightUnproven -= 1;
    };
  }

  /** A read of `key` came back 2xx: its path is known to work. */
  recordSuccess(key: string): void {
    this.#setPathOk(pathOf(key), true);
  }

  /** A read of `key` came back with a failure status (FAILURE_STATUSES). */
  recordFailure(key: string): void {
    this.#setPathOk(pathOf(key), false);
    const now = this.#now();
    const fails = (this.#failures.get(key) ?? []).filter((t) => t > now - FAIL_WINDOW_MS);
    fails.push(now);
    this.#failures.set(key, fails);
    if (fails.length >= FAIL_MAX) this.#failBlockedUntil.set(key, now + FAIL_BLOCK_MS);
    this.#expireBudget('fail', now).push(now);
    this.#onSpend?.('fail');
  }

  #setPathOk(path: string, ok: boolean): void {
    this.#pathOk.delete(path); // re-insert so the oldest path is evicted first
    this.#pathOk.set(path, ok);
    if (this.#pathOk.size > MAX_TRACKED_PATHS) {
      const oldest = this.#pathOk.keys().next().value;
      if (oldest !== undefined) this.#pathOk.delete(oldest);
    }
  }

  /** Another tab spent a page budget; count it here too. Capped at the budget:
   * a burst of messages can at most spend it, never pile up beyond. */
  recordRemote(kind: BudgetKind): void {
    const now = this.#now();
    const q = this.#expireBudget(kind, now);
    if (q.length < (kind === 'rapid' ? PAGE_RAPID_MAX : PAGE_FAIL_MAX)) q.push(now);
  }

  /** Reads counted against `kind` in the current window (for reports). */
  spent(kind: BudgetKind): number {
    return this.#expireBudget(kind, this.#now()).length;
  }

  /** True at most once per key per REPORT_EVERY_MS — keeps the console readable. */
  shouldReport(key: string): boolean {
    const now = this.#now();
    const last = this.#reportedAt.get(key);
    if (last !== undefined && now - last < REPORT_EVERY_MS) return false;
    this.#reportedAt.set(key, now);
    return true;
  }

  #expireBudget(kind: BudgetKind, now: number): number[] {
    const q = this.#budget[kind];
    const cutoff = now - PAGE_WINDOW_MS;
    while (q.length && q[0] <= cutoff) q.shift();
    return q;
  }

  /** Forget keys that are neither blocked, hit within the window, nor attempted
   * within LOOP_EXTEND_GAP_MS — so forgetting one cannot hide a rapid repeat. */
  #prune(now: number, current: string): void {
    const cutoff = now - LOOP_WINDOW_MS;
    for (const key of new Set([...this.#lastAttempt.keys(), ...this.#blockedUntil.keys(), ...this.#failBlockedUntil.keys()])) {
      if (key === current) continue;
      const until = this.#blockedUntil.get(key);
      if (until !== undefined && until > now) continue;
      if ((this.#failBlockedUntil.get(key) ?? 0) > now) continue;
      if ((this.#failures.get(key) ?? []).some((t) => t > now - FAIL_WINDOW_MS)) continue;
      if ((this.#hits.get(key) ?? []).some((t) => t > cutoff)) continue;
      if (now - (this.#lastAttempt.get(key) ?? Number.NEGATIVE_INFINITY) < LOOP_EXTEND_GAP_MS) continue;
      const s = this.#strikes.get(key);
      // An expired block that was never followed by an attempt has no release
      // time yet; it ended at `until`.
      if (s && s.releasedAt === Number.POSITIVE_INFINITY && until !== undefined) s.releasedAt = until;
      if (s && now - s.releasedAt >= LOOP_REPEAT_WITHIN_MS) this.#strikes.delete(key);
      this.#hits.delete(key);
      this.#blockedUntil.delete(key);
      this.#lastAttempt.delete(key);
      this.#failures.delete(key);
      this.#failBlockedUntil.delete(key);
    }
    for (const [key, t] of this.#reportedAt) {
      if (now - t >= REPORT_EVERY_MS) this.#reportedAt.delete(key);
    }
  }
}

export function isGuardedMethod(method: string | undefined): boolean {
  const m = (method ?? 'GET').toUpperCase();
  return m === 'GET' || m === 'HEAD';
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
