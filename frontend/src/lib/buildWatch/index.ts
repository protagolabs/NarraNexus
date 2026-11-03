/**
 * @file_name: index.ts (lib/buildWatch)
 * @date: 2026-11-02
 * @description: Notices when the cloud frontend was redeployed under an open tab.
 *
 * A browser tab keeps running the bundle it loaded until it is reloaded. On
 * 2026-11-02 a tab opened before the 10-30 fix was still running the old
 * channel-panel request loop days later (~600 credential reads a minute, no
 * loop guard — both fixes shipped in that release), while every freshly
 * loaded tab was fine. Nothing told the user that a reload would fix it.
 *
 * Each build stamps its id into the bundle (`__APP_BUILD_ID__`) and emits the
 * same id as `/build.json` (./contract.ts, wired in vite.config.ts). This
 * module re-reads that file and reports when the deployed id differs from the
 * running one, so the UI can offer a reload. Cloud web only: the desktop app
 * ships its frontend inside the app and has its own updater (UpdateBanner).
 *
 * `/build.json` is served by the catch-all location, which sends no-cache
 * headers; the query string defeats any intermediate cache anyway. A missing
 * file falls back to index.html on the SPA server, which fails to parse and
 * reads as "unknown" — never as a new build.
 */
import { guardedFetch } from '../guardedFetch';
import { BUILD_JSON_FILE, readBuildId } from './contract';

/** A read that has not answered by then counts as unknown (it must never wedge the watch). */
export const BUILD_CHECK_TIMEOUT_MS = 15_000;

/** How often an open tab re-checks the deployed build. */
export const BUILD_CHECK_INTERVAL_MS = 5 * 60_000;

/** Consecutive unknown reads (~15 min) before the watch says it is blind. */
export const UNKNOWN_STREAK_WARN = 3;

/** The build id this tab is running. */
export const RUNNING_BUILD_ID: string = __APP_BUILD_ID__;

/** `AbortSignal.timeout` where it exists (Safari < 16 lacks it), a timer otherwise. */
function timeoutSignal(ms: number): AbortSignal {
  if (typeof AbortSignal.timeout === 'function') return AbortSignal.timeout(ms);
  const controller = new AbortController();
  setTimeout(() => controller.abort(), ms);
  return controller.signal;
}

/** The deployed build id, or null when it cannot be read (offline, old deploy, dev server). */
export async function fetchDeployedBuildId(): Promise<string | null> {
  try {
    const res = await guardedFetch(`/${BUILD_JSON_FILE}?t=${Date.now()}`, {
      cache: 'no-store',
      signal: timeoutSignal(BUILD_CHECK_TIMEOUT_MS),
    });
    if (!res.ok) return null;
    return readBuildId(await res.json());
  } catch {
    return null;
  }
}

/**
 * Check every BUILD_CHECK_INTERVAL_MS and whenever the tab becomes visible
 * again (a tab left in the background for days is exactly the stale case).
 * No check at start: the page was just loaded from the current deployment.
 * Calls `onNewBuild` with the deployed id once a different build is seen,
 * then stops checking. Returns a stop function.
 *
 * Unknown never counts as new, but a watch that can never read the file is a
 * broken feature: after UNKNOWN_STREAK_WARN unknowns in a row it warns once
 * per streak (a successful read re-arms it).
 */
export function startBuildWatch(
  onNewBuild: (deployedId: string) => void,
  running: string = RUNNING_BUILD_ID,
): () => void {
  let stopped = false;
  let inFlight = false;
  let unknownStreak = 0;

  const stop = () => {
    stopped = true;
    window.clearInterval(timer);
    document.removeEventListener('visibilitychange', onVisible);
  };

  const check = async () => {
    if (stopped || inFlight) return;
    inFlight = true;
    try {
      const deployed = await fetchDeployedBuildId();
      if (stopped) return;
      if (!deployed) {
        unknownStreak += 1;
        if (unknownStreak === UNKNOWN_STREAK_WARN) {
          console.warn(
            `[build-watch] /${BUILD_JSON_FILE} unreadable ${UNKNOWN_STREAK_WARN} times in a row; ` +
              'this tab cannot tell when a new frontend is deployed',
          );
        }
        return;
      }
      unknownStreak = 0;
      if (deployed !== running) {
        stop();
        onNewBuild(deployed);
      }
    } finally {
      inFlight = false;
    }
  };

  const onVisible = () => {
    if (document.visibilityState === 'visible') void check();
  };

  const timer = window.setInterval(() => void check(), BUILD_CHECK_INTERVAL_MS);
  document.addEventListener('visibilitychange', onVisible);
  return stop;
}
