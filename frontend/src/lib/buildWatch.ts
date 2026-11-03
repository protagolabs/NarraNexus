/**
 * @file_name: buildWatch.ts
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
 * same id as `/build.json` (vite.config.ts). This module re-reads that file
 * and reports when the deployed id differs from the running one, so the UI
 * can offer a reload. Cloud web only: the desktop app ships its frontend
 * inside the app and has its own updater (UpdateBanner).
 *
 * `/build.json` is served by the catch-all location, which sends no-cache
 * headers; the query string defeats any intermediate cache anyway. A missing
 * file falls back to index.html on the SPA server, which fails to parse and
 * reads as "unknown" — never as a new build.
 */
import { BUILD_JSON_FILE, readBuildId } from './buildIdContract';
import { guardedFetch } from './guardedFetch';

/** A read that has not answered by then counts as unknown (it must never wedge the watch). */
export const BUILD_CHECK_TIMEOUT_MS = 15_000;

/** How often an open tab re-checks the deployed build. */
export const BUILD_CHECK_INTERVAL_MS = 5 * 60_000;

/** The build id this tab is running. */
export const RUNNING_BUILD_ID: string = __APP_BUILD_ID__;

/** The deployed build id, or null when it cannot be read (offline, old deploy, dev server). */
export async function fetchDeployedBuildId(): Promise<string | null> {
  try {
    const res = await guardedFetch(`/${BUILD_JSON_FILE}?t=${Date.now()}`, {
      cache: 'no-store',
      signal: AbortSignal.timeout(BUILD_CHECK_TIMEOUT_MS),
    });
    if (!res.ok) return null;
    return readBuildId(await res.json());
  } catch {
    return null;
  }
}

/**
 * Check now, then every BUILD_CHECK_INTERVAL_MS and whenever the tab becomes
 * visible again (a tab left in the background for days is exactly the stale
 * case). Calls `onNewBuild` with the deployed id once a different build is
 * seen, then stops checking. Returns a stop function.
 */
export function startBuildWatch(
  onNewBuild: (deployedId: string) => void,
  running: string = RUNNING_BUILD_ID,
): () => void {
  let stopped = false;
  let inFlight = false;

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
      if (!stopped && deployed && deployed !== running) {
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
  void check();
  return stop;
}
