---
code_file: frontend/src/components/__tests__/WebUpdateBanner.test.tsx
last_verified: 2026-11-02
stub: false
---

# WebUpdateBanner.test.tsx — the cloud "new version" pill

Locks [[WebUpdateBanner.tsx]]: hidden until the build watch reports a new
build, then shown with a Reload button that calls `location.reload()`;
dismissible for the tab; the watch is never started outside forced-cloud web
(not forced cloud, or running in Tauri).
