---
code_file: frontend/src/pages/RegisterPage.tsx
last_verified: 2026-04-16
stub: false
---

## 2026-04-16 addition — system-quota welcome banner

On successful cloud-mode registration, if `response.has_system_quota`
is true, a brief inline banner appears below the form showing the
initial token allotment and the page auto-navigates to `/` after
1.8s. The delay is short enough that the UX remains "fast enough"
but long enough to make the message readable. Local mode never seeds
the quota so the flag is always false there and the banner is
skipped — no layout flash.

# RegisterPage.tsx — Cloud-mode account registration with invite code

## Why it exists

Cloud deployments require invite-code gating to control who can create accounts. `RegisterPage` is the cloud-only counterpart to `CreateUserDialog` (local mode). After successful registration, it auto-logs in and navigates to the main app, so the user never needs to separately log in.

## Upstream / Downstream

Route: `/register`, wrapped by `PublicRoute`. Only reachable from `LoginPage` when `mode` is `cloud-app` or `cloud-web`.

Calls `api.register(userId, password, inviteCode)`, which maps to `POST /api/auth/register`. On success, stores the returned JWT via `configStore.login()`, fetches agents, and navigates to `/`.

## Design decisions

**Auto-login after registration.** Calling `configStore.login(userId, token, 'user')` immediately after a successful register is deliberate — the user should land in the app without a separate login step. The role is hardcoded as `'user'` since the registration endpoint does not support creating staff accounts.

**Shares "Change Mode" logic with `LoginPage`.** Both pages hide the "Change Mode" button when `mode === 'cloud-web'` and both call `setCloudApiUrl(''); setMode(null)` before navigating to `/mode-select`. The behavior is identical — keeping it duplicated (rather than extracted into a hook) is a conscious simplicity tradeoff.

**No email verification.** The invite code IS the verification gate. If the system requires stronger verification in the future, this page needs a post-registration confirmation step.

## Gotchas

**Agents fetch on registration is best-effort.** A newly registered user will have no agents. The `try {}` around `api.getAgents` swallows errors so a missing agent does not block the navigation to `/`. `RootRedirect` will then navigate to `/setup` or `/app/chat` depending on whether providers are configured.

**Client-side validations can be bypassed.** Username length (2-32), password length (6+), and password match checks are client-side only. The backend has its own validation and will return `success: false` with an appropriate `error` message if any constraint is violated.
