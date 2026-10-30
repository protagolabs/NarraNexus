/**
 * @file_name: apiError.ts
 * @author: Bin Liang
 * @date: 2026-10-30
 * @description: Error types shared by every network caller — the API client and the guarded fetch underneath it.
 *
 * Lives on its own so `guardedFetch.ts` (which `api.ts` builds on) can throw
 * `RequestLoopBlockedError` without an import cycle. `api.ts` re-exports both,
 * so callers keep importing them from `@/lib/api`.
 */
import i18n from '@/i18n';

/**
 * Error thrown for non-2xx API responses. Carries the HTTP status so
 * callers can branch on it (e.g. treat DELETE 404 as already-gone)
 * instead of string-matching the message.
 */
export class ApiError extends Error {
  readonly status: number;
  /** The backend's raw HTTPException `detail` ('' when the body had
   * none) — callers that show user copy want this, not the prefixed
   * message. */
  readonly detail: string;

  constructor(status: number, message: string, detail: string = '') {
    super(message);
    this.name = 'ApiError';
    this.status = status;
    this.detail = detail;
  }
}

/**
 * A read refused locally by the request-loop guard; it never reached the
 * network. `status` 0 so no caller mistakes it for a server answer. The message
 * is user-facing (many panels render `e.message` directly), so it is localized
 * and carries no URL; the endpoint goes to the console report instead.
 */
export class RequestLoopBlockedError extends ApiError {
  /** 'slow': no in-flight slot freed in time — the server is slow, not the user
   * sending too much, so the message says that instead. */
  constructor(reason: 'limit' | 'slow' = 'limit') {
    super(0, i18n.t(reason === 'slow' ? 'common.requestSlow' : 'common.requestBlocked'));
    this.name = 'RequestLoopBlockedError';
  }
}
