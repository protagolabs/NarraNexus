---
code_file: frontend/src/components/FloatingPill.tsx
last_verified: 2026-11-02
stub: false
---

# FloatingPill.tsx — the top-center notice pill

## Why it exists

[[UpdateBanner.tsx]] (desktop updater ready) and [[WebUpdateBanner.tsx]]
(cloud frontend redeployed) are the same surface: a floating pill at the top
center with an icon, a sentence, one action and a dismiss. They carried
hand-copied class strings, and the copy is where a wrong z value crept in
(review of PR #441). One component now owns the chrome; callers pass icon,
text, action and dismiss.

## Decisions

- **No bespoke z value.** The pill uses the shared `z-50` layer and stacks by
  DOM order. App mounts the pills after the amber top banners and before the
  routes, so they show over the amber banners and under full-screen dialogs
  (`SettingsModal`, `SignUpDialog`, … are `z-50` inside the routes). An update
  action must never float over a half-filled dialog.
- The wrapper is `pointer-events-none` and only the pill takes clicks, so the
  workspace beside it stays usable.
- Text is the caller's: UpdateBanner keeps its hard-coded English, the web
  banner uses i18n. Unifying that is a separate change.
- `ArenaProvisioningModal` is not built on this: it is a rounded card with
  progress state and its own surface tokens, not this pill; folding it in
  would change its look.
