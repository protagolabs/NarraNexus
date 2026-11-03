---
code_file: frontend/src/lib/buildWatch/index.ts
last_verified: 2026-11-02
stub: false
---

# lib/buildWatch/index.ts — has the cloud frontend been redeployed under this tab?

## Why it exists

A browser tab runs the bundle it loaded until it is reloaded. On 2026-11-02 a
user's tab opened before the 10-30 release was still running the old
channel-panel credential loop days later (~600 reads a minute in prod; the same
v1.21.13 build reproduces 5250 reads in 15 s locally against 12 on the current
build). Both the loop fix and the loop guard shipped in that release, so the
stale tab had neither, while every freshly loaded tab was fine. Nothing told the
user a reload would fix it. This module is the detection half; the UI half is
[[WebUpdateBanner.tsx]].

## How it decides

- Each build gets a unique id (`version+timestamp`) stamped into the bundle as
  `__APP_BUILD_ID__` and emitted as `dist/build.json` (`vite.config.ts`
  `emitBuildId` plugin). Two builds of the same version still differ.
- `startBuildWatch` reads `/build.json` every 5 minutes and whenever the tab
  becomes visible again (a tab parked in the background for days is the stale
  case). Not at start: the page was just loaded from the current deployment,
  so that read would only ever match — and it would take one of the loop
  guard's in-flight slots during the cold-start burst. On the first id that differs from the running one it calls
  back once and stops — the tab cannot become "more stale", and polling on
  would only be traffic.
- Overlapping checks are dropped (`inFlight`), so a visibility flip during a
  slow read does not stack requests. Every read carries a 15 s abort signal
  (`AbortSignal.timeout`, or a timer-backed controller where it is missing —
  Safari < 16): the underlying fetch has no timeout of its own,
  and one hung read would otherwise leave `inFlight` set and the tab never
  checking again. A result that lands after `stop()` is dropped.
- The file name and field come from [[contract.ts]] (same package), shared with the
  emitting Vite plugin.
- The id is `version+build-time`, not content-derived, so a `--no-cache`
  rebuild of unchanged source (deploy repo `app-rebuild*` targets) offers open
  tabs a reload they did not strictly need. Accepted: harmless, and a
  content-derived id would have to fingerprint the entry chunk.

## Gotchas

- **Unknown is never "new" — but never silent either.** After 3 unknown reads
  in a row (~15 min) the watch warns once in the console (`[build-watch]`),
  once per streak; a successful read re-arms it. Otherwise a deployment path
  that cannot serve the file (another nginx, a CDN returning 404) would leave
  the feature dead with no trace.
- **What reads as unknown.** A deploy without `build.json` (older image) gets
  the SPA's `index.html` back from nginx's catch-all `try_files`; that fails to
  parse and reads as `null`. Same for HTTP errors, network errors and a
  non-string id. Only a real, different id raises the banner.
- **Caching.** `/build.json` falls under the frontend nginx `location /`, which
  already sends `no-cache, no-store` (deploy repo `docker/nginx.conf`); the
  `?t=` query and `cache: 'no-store'` defeat any intermediate cache anyway. No
  deploy-repo change is needed.
- Goes through `guardedFetch` like every read (the no-raw-fetch scan enforces
  it). Each check has a distinct `?t=` URL, so the per-URL loop rule never
  sees repeats; at one read per 5 min it is far below the page budget.
- The dev server emits no `build.json` (the plugin is `apply: 'build'`), so a
  dev tab never shows the banner.
