---
code_file: frontend/src/components/WebUpdateBanner.tsx
last_verified: 2026-11-02
stub: false
---

# WebUpdateBanner.tsx — cloud web "new version, reload" pill

## Why it exists

The web counterpart of [[UpdateBanner.tsx]] (which follows the desktop
updater). After a cloud deploy, an open tab keeps running the old bundle —
including bugs the deploy fixed — until reloaded; see [[index.ts]] (lib/buildWatch) for the
2026-11-02 case (a stale tab still running the pre-10-30 channel loop). This
pill appears once the deployed build differs from the running one.

## Decisions

- **Never reloads by itself.** The user may be mid-message; the reload is
  theirs to trigger. Dismiss hides it for this tab only.
- **Cloud web only.** App mounts it lazily only when `isForcedCloud() &&
  !isTauri()`, so desktop / local never download its chunk and the entry chunk
  stays within `budget.json` (mounting it eagerly put the entry 862 bytes
  over). The component repeats the check so it is correct wherever mounted.
- The pill itself is [[FloatingPill.tsx]], shared with UpdateBanner. It sits
  at the shared `z-50` layer and stacks by DOM order: App mounts it after the
  amber top banners (readable over them) and before the routes, so full-screen
  dialogs cover it — a Reload click must never land on top of a half-filled
  dialog. Text comes from `appBanners.newWebVersion` /
  `appBanners.reloadNow` / `appBanners.dismiss`, present in all 10 locales.
