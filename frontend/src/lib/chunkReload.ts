/**
 * @file_name: chunkReload.ts
 * @author: NarraNexus
 * @date: 2026-08-12
 * @description: One-shot page reload when a lazily-imported route chunk 404s
 * after a deploy.
 *
 * The app code-splits routes with React.lazy (App.tsx). When a new build ships,
 * an already-open tab still references the OLD hashed chunk filenames; the next
 * navigation's dynamic import 404s and Vite fires a `vite:preloadError` event on
 * window. A single reload fetches the new index.html + chunk manifest and
 * recovers — but a genuinely broken build (the chunk truly 404s forever) must
 * not wedge the tab in an endless reload loop, so the reload fires at most once
 * per browsing session. A second deploy in the same session (rare) is caught by
 * ChunkErrorBoundary's manual "refresh" prompt instead.
 */

const RELOAD_GUARD_KEY = 'nx-chunk-reloaded';

/**
 * Reload once per session. `reload` and `storage` are injected so the guard is
 * testable without touching the real window/sessionStorage.
 */
export function handlePreloadError(
  reload: () => void,
  storage: Pick<Storage, 'getItem' | 'setItem'> = sessionStorage,
): void {
  if (storage.getItem(RELOAD_GUARD_KEY)) return; // already reloaded this session
  storage.setItem(RELOAD_GUARD_KEY, '1');
  reload();
}

/** Wire the `vite:preloadError` recovery. Call once at startup (main.tsx). */
export function installChunkReload(): void {
  window.addEventListener('vite:preloadError', (event) => {
    // Suppress the default uncaught-rejection; we handle recovery ourselves.
    event.preventDefault();
    handlePreloadError(() => window.location.reload());
  });
}
