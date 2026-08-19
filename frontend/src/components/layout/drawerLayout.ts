/**
 * @file_name: drawerLayout.ts
 * @author: NarraNexus
 * @date: 2026-08-19
 * @description: Sizing and persistence rules for the bookmark drawer.
 *
 * Kept apart from MainLayout so the policy (how wide may the drawer get,
 * what does a fresh profile default to) is unit-testable without mounting
 * the whole chat view.
 */

export const DRAWER_PINNED_KEY = 'bookmark_drawer_pinned_v1';
export const DRAWER_OPENED_ONCE_KEY = 'bookmark_drawer_opened_v1';
export const DRAWER_WIDTH_KEY = 'bookmark_drawer_width_v1';
export const DRAWER_FIRST_RUN_KEY = 'bookmark_drawer_first_run_v1';

export const DEFAULT_DRAWER_PX = 400;
export const MIN_DRAWER_PX = 300;
// Reserved room the drawer may never eat into: the sidebar (272) plus the
// chat column's minimum (400). Within that, the drawer can grow to 60% of
// the viewport — an artifact wants real estate, and "as wide as half the
// screen" is the whole point of the drawer replacing the skinny side column.
export const DRAWER_VIEWPORT_RESERVE_PX = 672;
export const DRAWER_MAX_VIEWPORT_FRACTION = 0.6;

/** Largest width the pinned drawer may take on this viewport. */
export function maxDrawerPx(viewportW: number): number {
  return Math.max(
    MIN_DRAWER_PX,
    Math.min(viewportW * DRAWER_MAX_VIEWPORT_FRACTION, viewportW - DRAWER_VIEWPORT_RESERVE_PX),
  );
}

export function clampDrawerWidth(px: number, viewportW: number): number {
  return Math.min(maxDrawerPx(viewportW), Math.max(MIN_DRAWER_PX, px));
}

/** Pinned is the default: panels should stay put until the user says
 *  otherwise. Only an explicit unpin (stored '0') turns it off. */
export function readInitialDrawerPinned(storage: Pick<Storage, 'getItem'>): boolean {
  return storage.getItem(DRAWER_PINNED_KEY) !== '0';
}

/**
 * First run only (desktop): should the artifacts panel auto-open with the
 * coach mark? A brand-new user has to SEE where artifacts land before the
 * panel is worth closing. Read-only — render-safe; the caller marks the
 * first run as seen from an effect (markFirstRunSeen), so a discarded
 * render pass cannot burn the marker. The marker is its OWN key: opening a
 * panel by hand (any viewport) writes DRAWER_OPENED_ONCE_KEY, and a phone
 * visit must not spend the desktop coach mark.
 */
export function shouldAutoOpenFirstRun(
  storage: Pick<Storage, 'getItem'>,
  isSmallViewport: boolean,
): boolean {
  try {
    return !isSmallViewport && !storage.getItem(DRAWER_FIRST_RUN_KEY);
  } catch {
    return false;
  }
}

export function markFirstRunSeen(storage: Pick<Storage, 'setItem'>): void {
  try {
    storage.setItem(DRAWER_FIRST_RUN_KEY, '1');
  } catch {
    /* storage unavailable — the coach may show again; harmless */
  }
}
