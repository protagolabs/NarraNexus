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
 *    or never answered with a success) are refused until the window drains; paths whose
 *    last answer was a success (200–399, `recordSuccess`) keep working, so a burst of
 *    403s from one panel cannot freeze the agent list or the chat. Reads of
 *    unproven paths also hold a slot while in flight (`acquire`): landed
 *    failures plus slots held may not reach EDGE_FAIL_SAFE (18), and a read
 *    that finds no slot waits for one (first come, first served; refused after
 *    SLOT_WAIT_MAX_MS, never let through). So in one tab a burst of first
 *    reads of any size — a crafted link listing dozens of foreign agents
 *    included — reaches the server at most 18 at a time, and stays under the
 *    edge's line even if every one fails. Slots are per tab (only landed
 *    failures are shared across tabs), so two tabs bursting at the same
 *    moment can together exceed it. Both page-wide limits are off for a known-local
 *    backend (no edge in front of it; `edgeLimitsEnabled`, fail-closed in
 *    guardedFetch); the per-URL failure block stays. The edge monitor (sentinel,
 *    monitoring v2) bans an IP at 20 probing-like failures a minute
 *    (400/403/404/405), so a looping client must stay below that line on its
 *    own. Limit: a path whose answers alternate between success and failure stays
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
/** Longest a read waits for an in-flight slot before it is refused (a slot is
 * held until the response headers arrive, so a slow or failing backend can hold
 * all of them; the read then fails visibly instead of spinning forever — it is
 * never let through, which would break the bound). */
export const SLOT_WAIT_MAX_MS = 15_000;
/** Most reads that may wait for a slot at once; past it a read is refused as
 * 'slot_full' (after the per-URL refusal delay, so a retry loop is slowed
 * rather than spinning). Bounds the queue and its timers under a loop whose
 * reads never settle. */
export const MAX_SLOT_WAITERS = 200;
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

export type LoopVerdict = 'ok' | 'loop' | 'failing' | 'rapid_budget' | 'fail_budget' | 'slot_wait' | 'slot_full';
export type BudgetKind = 'rapid' | 'fail';

export interface RequestLoopGuardOptions {
  /** The guard's clock. Must agree with the global timer source: the slot
   * queue's wait limit and failure-expiry wake use setTimeout. */
  now?: Clock;
  /** Called when an admitted request spends a page budget (to tell other tabs). */
  onSpend?: (kind: BudgetKind) => void;
  /** Whether the page-wide failure limits apply right now (only behind the edge). */
  edgeLimitsEnabled?: () => boolean;
  /** Called when a read starts waiting for an in-flight slot (for reports). */
  onSlotWait?: () => void;
}

/** Why `acquire` refused a read instead of granting a slot. */
export type SlotRefusal = 'fail_budget' | 'failing' | 'slot_wait' | 'slot_full';

interface SlotWaiter {
  key: string;
  resolve: (value: (() => void) | SlotRefusal) => void;
  reject: (reason: unknown) => void;
  signal: AbortSignal | null;
  timer: ReturnType<typeof setTimeout> | null;
  onAbort: (() => void) | null;
}

export class RequestLoopGuard {
  readonly #hits = new Map<string, number[]>();
  readonly #blockedUntil = new Map<string, number>();
  readonly #lastAttempt = new Map<string, number>();
  readonly #strikes = new Map<string, { count: number; releasedAt: number }>();
  readonly #failures = new Map<string, number[]>();
  readonly #failBlockedUntil = new Map<string, number>();
  /** Last outcome per path (true = success 200–399, false = failure status); insertion-ordered, capped. */
  readonly #pathOk = new Map<string, boolean>();
  #inflightUnproven = 0;
  readonly #slotQueue: SlotWaiter[] = [];
  #expiryTimer: ReturnType<typeof setTimeout> | null = null;
  readonly #edgeLimitsEnabled: () => boolean;
  readonly #onSlotWait?: () => void;
  readonly #budget: Record<BudgetKind, number[]> = { rapid: [], fail: [] };
  readonly #reportedAt = new Map<string, number>();
  readonly #now: Clock;
  readonly #onSpend?: (kind: BudgetKind) => void;

  constructor(options: RequestLoopGuardOptions | Clock = {}) {
    const opts = typeof options === 'function' ? { now: options } : options;
    this.#now = opts.now ?? (() => Date.now());
    this.#onSpend = opts.onSpend;
    this.#edgeLimitsEnabled = opts.edgeLimitsEnabled ?? (() => true);
    this.#onSlotWait = opts.onSlotWait;
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
   * While the page's failure budget is spent (PAGE_FAIL_MAX failures landed,
   * edge limits on), a read is refused unless the last answer for its path
   * (see `pathOf`: agent_id kept, other query parameters dropped) was a
   * success. In-flight slots are handled by `acquire`, after this check.
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
      && failed >= PAGE_FAIL_MAX
      && this.#edgeLimitsEnabled()
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
   * An admitted read of `key` is about to go out. Reads of paths not known to
   * work (never answered with a success, or last answered with a failure) hold one of a
   * fixed number of slots while in flight: landed failures in the window plus
   * slots held may not reach EDGE_FAIL_SAFE. A read that finds no slot waits
   * for one (up to SLOT_WAIT_MAX_MS) instead of being refused at once — so a burst of first reads, however large,
   * reaches the server at most EDGE_FAIL_SAFE at a time, and even if every one
   * of them fails the page stays under the edge's line. Queued reads are served
   * first come, first served, re-checked when a slot is released, when the
   * oldest landed failure leaves the window, when a newcomer joins, and when a
   * path is proven; each is re-judged when served (a proven path or edge limits
   * switched off need no slot). Resolves to the release function
   * (call it once the read settles, however it settles), or to a refusal: the
   * page's failure budget filled ('fail_budget'), the read's URL got
   * fail-blocked ('failing'), no slot freed within SLOT_WAIT_MAX_MS
   * ('slot_wait'), or the queue itself is full ('slot_full').
   * Paths whose last answer was a success, and every read when the edge limits are off
   * (a known-local backend), take no slot.
   */
  acquire(key: string, signal?: AbortSignal | null): Promise<(() => void) | SlotRefusal> {
    if (this.#pathOk.get(pathOf(key)) === true || !this.#edgeLimitsEnabled()) return Promise.resolve(NOOP);
    return new Promise((resolve, reject) => {
      const waiter: SlotWaiter = { key, resolve, reject, signal: signal ?? null, timer: null, onAbort: null };
      // First come, first served: a newcomer never overtakes a queued read.
      if (this.#slotQueue.length === 0 && this.#tryGrant(waiter)) return;
      if (signal?.aborted) {
        reject(signal.reason ?? new DOMException('Aborted', 'AbortError'));
        return;
      }
      if (this.#slotQueue.length >= MAX_SLOT_WAITERS) {
        // Before refusing on a full queue, serve it once: capacity may be free
        // with nobody having woken it (see the drain after enqueueing below).
        this.#drainQueue();
        if (this.#slotQueue.length === 0 && this.#tryGrant(waiter)) return;
        if (this.#slotQueue.length >= MAX_SLOT_WAITERS) {
          resolve('slot_full');
          return;
        }
      }
      waiter.timer = setTimeout(() => {
        this.#dequeue(waiter);
        resolve('slot_wait');
      }, SLOT_WAIT_MAX_MS);
      if (signal) {
        waiter.onAbort = () => {
          this.#dequeue(waiter);
          reject(signal.reason ?? new DOMException('Aborted', 'AbortError'));
        };
        signal.addEventListener('abort', waiter.onAbort, { once: true });
      }
      this.#slotQueue.push(waiter);
      // Capacity may already be free without anyone having woken the queue
      // (failures that left the window before the expiry wake fired): serve
      // it in order now, this newcomer last.
      this.#drainQueue();
      if (this.#slotQueue.includes(waiter)) this.#onSlotWait?.();
    });
  }

  /** Grant `waiter` a slot, refuse it, or leave it waiting (returns false). */
  #tryGrant(waiter: SlotWaiter): boolean {
    // Re-judged on every attempt, not only on arrival: the path may have been
    // proven by another read meanwhile, or the edge limits switched off (the
    // mode resolved to local). Either way it needs no slot.
    if (this.#pathOk.get(pathOf(waiter.key)) === true || !this.#edgeLimitsEnabled()) {
      waiter.resolve(NOOP);
      return true;
    }
    const failed = this.#expireBudget('fail', this.#now()).length;
    if (failed >= PAGE_FAIL_MAX) {
      waiter.resolve('fail_budget');
      return true;
    }
    // Its URL got fail-blocked while it waited: the per-URL verdict applies.
    if ((this.#failBlockedUntil.get(waiter.key) ?? 0) > this.#now()) {
      waiter.resolve('failing');
      return true;
    }
    if (failed + this.#inflightUnproven >= EDGE_FAIL_SAFE) return false;
    this.#inflightUnproven += 1;
    let released = false;
    waiter.resolve(() => {
      if (released) return;
      released = true;
      this.#inflightUnproven -= 1;
      this.#drainQueue();
    });
    return true;
  }

  #dequeue(waiter: SlotWaiter): void {
    const i = this.#slotQueue.indexOf(waiter);
    if (i >= 0) this.#slotQueue.splice(i, 1);
    if (waiter.timer !== null) clearTimeout(waiter.timer);
    if (waiter.onAbort) waiter.signal?.removeEventListener('abort', waiter.onAbort);
    if (this.#slotQueue.length === 0) this.#clearExpiryWake();
  }

  /** Serve queued reads in order until the head cannot be served. */
  #drainQueue(): void {
    while (this.#slotQueue.length > 0) {
      const head = this.#slotQueue[0];
      if (!this.#tryGrant(head)) break;
      this.#dequeue(head);
    }
    this.#scheduleExpiryWake();
  }

  /** Landed failures leave the window over time: re-check the queue when the
   * oldest one does, not only when a slot is released. */
  #scheduleExpiryWake(): void {
    this.#clearExpiryWake();
    if (this.#slotQueue.length === 0) return;
    const oldest = this.#expireBudget('fail', this.#now())[0];
    if (oldest === undefined) return;
    this.#expiryTimer = setTimeout(() => {
      this.#expiryTimer = null;
      this.#drainQueue();
    }, Math.max(0, oldest + PAGE_WINDOW_MS - this.#now()) + 1);
  }

  #clearExpiryWake(): void {
    if (this.#expiryTimer !== null) clearTimeout(this.#expiryTimer);
    this.#expiryTimer = null;
  }

  /** Tests only: refuse queued reads and drop timers. */
  dispose(): void {
    for (const w of [...this.#slotQueue]) {
      this.#dequeue(w);
      w.resolve('slot_wait');
    }
    this.#clearExpiryWake();
  }

  /** A read of `key` came back with a success (200–399): its path is known to work. */
  recordSuccess(key: string): void {
    this.#setPathOk(pathOf(key), true);
    // Queued reads of this path no longer need a slot.
    if (this.#slotQueue.length > 0) this.#drainQueue();
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
