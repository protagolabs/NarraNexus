/**
 * @file_name: requestLoopGuard.ts
 * @author: Bin Liang
 * @date: 2026-10-30
 * @description: Client-side brake for runaway read loops — a URL fetched too often, rapid repeats, or reads past the edge ban line are refused locally instead of hitting the network.
 *
 * Why: a render loop (effect → fetch → setState → new deps → effect …) turns
 * one open panel into a sustained request flood. On 2026-10-30 the Channels
 * section did exactly that (~30 req/s) and the edge flood rule banned the
 * user's IP and suspended the account. Component fixes remove the known loop;
 * this is the backstop for the next one, so a frontend bug degrades to an
 * error on one panel instead of an account ban.
 *
 * Scope: reads (GET / HEAD) only — refusing a user's save is worse than the
 * flood. Two layers:
 *  - per URL (path + query): an identical URL repeated faster than any
 *    legitimate poll is blocked;
 *  - page-wide budgets shared by same-origin tabs (`recordRemote`, wired in
 *    guardedFetch.ts), because the edge counts per IP, not per tab: a "rapid"
 *    budget for the same URL again within LOOP_EXTEND_GAP_MS (stops loops over
 *    a few URLs early), and a total read budget just under the edge ban line:
 *    reads beyond it would get the IP banned anyway, so refusing them can only
 *    be better for the user than letting them through. guardedFetch switches the
 *    total budget off only when the backend is known to be local (desktop app,
 *    run.sh), where there is no edge ban and it would only refuse real work.
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
/** Page-wide budgets over PAGE_WINDOW_MS, shared across same-origin tabs. Every
 * admitted read spends the total budget; a rapid repeat also spends the rapid
 * budget. PAGE_TOTAL_MAX sits under the edge ban line (562/min per IP) with
 * room for writes, SSE and static assets; ordinary polling stays far below it. */
export const PAGE_WINDOW_MS = 60_000;
export const PAGE_RAPID_MAX = 150;
export const PAGE_TOTAL_MAX = 500;
const REPORT_EVERY_MS = 60_000;
const MAX_TRACKED_KEYS = 500;

type Clock = () => number;

export type LoopVerdict = 'ok' | 'loop' | 'rapid_budget' | 'total_budget';
export type BudgetKind = 'rapid' | 'total';

export interface RequestLoopGuardOptions {
  now?: Clock;
  /** Called when an admitted request spends a page budget (to tell other tabs). */
  onSpend?: (kind: BudgetKind) => void;
  /** Whether the total read budget applies right now (only behind the edge). */
  totalBudgetEnabled?: () => boolean;
}

export class RequestLoopGuard {
  readonly #hits = new Map<string, number[]>();
  readonly #blockedUntil = new Map<string, number>();
  readonly #lastAttempt = new Map<string, number>();
  readonly #strikes = new Map<string, { count: number; releasedAt: number }>();
  readonly #budget: Record<BudgetKind, number[]> = { rapid: [], total: [] };
  readonly #reportedAt = new Map<string, number>();
  readonly #now: Clock;
  readonly #onSpend?: (kind: BudgetKind) => void;
  readonly #totalBudgetEnabled: () => boolean;

  constructor(options: RequestLoopGuardOptions | Clock = {}) {
    const opts = typeof options === 'function' ? { now: options } : options;
    this.#now = opts.now ?? (() => Date.now());
    this.#onSpend = opts.onSpend;
    this.#totalBudgetEnabled = opts.totalBudgetEnabled ?? (() => true);
  }

  /**
   * Decide whether one read of `key` may go to the network.
   *
   * Per URL: past LOOP_MAX_PER_WINDOW inside LOOP_WINDOW_MS the key is blocked
   * (for longer if it is a repeat offender), and while blocked every rapid
   * attempt pushes the release out to a full LOOP_WINDOW_MS from now.
   *
   * Page: every admitted read spends the total budget and a rapid repeat also
   * the rapid budget; when either is spent the request is refused. A request
   * refused by a budget spends nothing and does not count toward the URL's
   * window; its attempt time is still recorded, so a loop that keeps retrying
   * keeps reading as rapid instead of slipping out every LOOP_EXTEND_GAP_MS.
   */
  check(key: string): LoopVerdict {
    const now = this.#now();
    const previous = this.#lastAttempt.get(key);
    this.#lastAttempt.set(key, now);
    // Bound the bookkeeping on every call, whatever the verdict: a loop that
    // keeps getting refused is exactly when the maps grow.
    if (this.#lastAttempt.size > MAX_TRACKED_KEYS) this.#prune(now, key);

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
    const total = this.#expireBudget('total', now);
    const rapidSpent = rapid ? this.#expireBudget('rapid', now) : null;
    if (rapidSpent && rapidSpent.length >= PAGE_RAPID_MAX) return 'rapid_budget';
    if (this.#totalBudgetEnabled() && total.length >= PAGE_TOTAL_MAX) return 'total_budget';
    total.push(now);
    this.#onSpend?.('total');
    if (rapidSpent) {
      rapidSpent.push(now);
      this.#onSpend?.('rapid');
    }
    this.#hits.set(key, hits);
    return 'ok';
  }

  /** Another tab spent a page budget; count it here too. Capped at the budget:
   * a burst of messages can at most spend it, never pile up beyond. */
  recordRemote(kind: BudgetKind): void {
    const now = this.#now();
    const q = this.#expireBudget(kind, now);
    if (q.length < (kind === 'rapid' ? PAGE_RAPID_MAX : PAGE_TOTAL_MAX)) q.push(now);
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
    for (const key of new Set([...this.#lastAttempt.keys(), ...this.#blockedUntil.keys()])) {
      if (key === current) continue;
      const until = this.#blockedUntil.get(key);
      if (until !== undefined && until > now) continue;
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
