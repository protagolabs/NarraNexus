---
code_file: frontend/src/lib/clarity.ts
last_verified: 2026-07-16
stub: false
---

# clarity.ts — cloud-only Microsoft Clarity tracking snippet

## Why this file exists

Microsoft Clarity is a hosted analytics SaaS (heatmaps + session
recordings). We don't run any Clarity infrastructure ourselves — we only
need to inject the official bootstrap snippet on pages served by the
forced-cloud deploy (`agent.narra.nexus`). This file is that injector.

## Design decisions

- **Cloud-only, gated on `isForcedCloud()`**: desktop (Tauri) typically runs
  against `localhost`, where session-recording analytics has no useful
  signal; local self-host builds have no equivalent privacy/consent context
  that the cloud deploy does. `isForcedCloud()` (from `runtimeConfig.ts`) is
  the single existing authority for "is this a cloud deploy" — reused as-is,
  no new detection logic.
- **Project id compiled in, not runtime-injected via `/config.js`**: the
  Clarity project id (`xnaag1qmu0`) is not a secret — Clarity's own snippet
  design puts it in plaintext in the page source — so baking it into the
  frontend build avoids a round-trip through the external deploy repo that
  overwrites `/config.js` at container start. Trade-off: if a future
  environment (e.g. `dev-agent.narra.nexus`) wants a *different* Clarity
  project, that requires a code change + rebuild, not just a deploy-config
  edit.
- **Idempotent via marker attribute**: `initClarity()` may run twice under
  React `StrictMode` / HMR; the `data-clarity-project-id` attribute on the
  injected `<script>` is checked before inserting a second one.

## Upstream / downstream

- **Called by**: `frontend/src/main.tsx`, once at boot, right after
  `installExternalLinkInterceptor()`.
- **Reads**: `isForcedCloud()` from `frontend/src/lib/runtimeConfig.ts`.

## Gotcha / edge cases

- **Trigger**: running `npm run dev`, the Tauri desktop build, or a local
  self-host (`bash run.sh`) deploy. **Symptom**: no network request to
  `clarity.ms` is ever made, `window.clarity` is never defined. **Root
  cause**: this is expected — `isForcedCloud()` is `false` in all three of
  those runtimes, so `initClarity()` returns immediately. Not a bug.

## Related constraints

- Iron rule #10 — mirror md must be updated in the same commit as any
  behavioural change to this file.
