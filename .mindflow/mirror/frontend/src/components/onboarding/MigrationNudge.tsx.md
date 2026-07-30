---
code_file: frontend/src/components/onboarding/MigrationNudge.tsx
last_verified: 2026-07-30
stub: false
---

# onboarding/MigrationNudge.tsx — local-mode "import an existing agent" nudge

## Why it exists

The startup prompt of Agent Migration (Owner's item 2): on the local/desktop app,
after login, detect other-framework agents on the machine and offer a dismissible
card that deep-links into [[ImportAgentModal]]. Rendered by [[MainLayout]] next to
[[OnboardingChecklist]].

## Design decisions

- **Local only.** Gated on `useRuntimeStore.mode === 'local'` — cloud has no user
  filesystem (detect 503s there), so cloud never mounts the nudge.
- **Per-machine dismissal** (`localStorage` `nn_migration_nudge_dismissed_v1`):
  detection is filesystem-local, so a user on another machine should be nudged
  there too — an account-level flag (users.metadata) would wrongly suppress it.
- Fires `api.migrateDetect()` on mount (best-effort, silent on failure); renders
  only when ≥1 framework is found. The per-project Claude rows are deduped to
  distinct framework labels for the message.
- On apply, mirrors [[AgentList]]'s `handleImportApplied` (refresh agents + select
  the new one + navigate) and dismisses itself.

## Gotcha

- Shows once local + logged-in + undismissed + something detected — it does NOT
  hard-gate on "settings configured"; importing an agent is itself valid setup.
