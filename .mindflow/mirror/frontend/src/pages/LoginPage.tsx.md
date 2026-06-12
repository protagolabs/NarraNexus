---
code_file: frontend/src/pages/LoginPage.tsx
last_verified: 2026-06-12
stub: false
---

## 2026-06-12 — "Forgot password?" entry

Cloud branch gained a "Forgot password?" link under the password field
(`showForgot` state) that opens [[ForgotPasswordCard.tsx]], rendered next to the
CreateUser / AuthBind dialogs. Local branch untouched.

## 2026-06-11 — cloud branch → NetMind login

Cloud branch replaced: email + password login via `useNetmindAuth.emailLogin`, three OAuth buttons (Google, Microsoft, GitHub) via `useNetmindAuth.startOAuth`, bind dialog via `AuthBindDialog` when `netmind.bindInfo` is set, and Sign-up as an external `<a href={getNetmindConfig().registerUrl}>` link. `api.login` is no longer called in cloud mode. Local branch untouched.

# LoginPage.tsx — Dual-mode login (local user_id / cloud NetMind email+OAuth)

## Why it exists

The login experience differs based on deployment mode. Local mode has no password — just a user_id that acts as a local identity. Cloud mode uses NetMind.AI authentication: email + password login, or OAuth (Google, Microsoft, GitHub). A single component handles both variants by reading `runtimeStore.mode` and rendering entirely different form subtrees.

## Upstream / Downstream

Route: `/login`, wrapped by `PublicRoute` in `App.tsx` (redirects to `/` if already logged in).

Reads `mode` from `runtimeStore` to determine which branch to render.

**Cloud branch**: uses `useNetmindAuth` hook for all auth actions. On success (via `onSuccess` callback): calls `configStore.login(userId, token, role, {displayName, email})`, `configStore.setNetmindToken(loginToken)`, fetches agents, then navigates. OAuth flows open a popup that sends a `postMessage`; `useNetmindAuth` handles the callback internally. When OAuth returns a `bindInfo`, renders `AuthBindDialog` as a modal. Sign-up link is an external `<a>` pointing to `getNetmindConfig().registerUrl`. `api.login` is NOT called in cloud mode.

**Local branch**: unchanged. Calls `api.login(userId)` directly, then `configStore.login`, then agents. Renders `CreateUserDialog` modal for the "Create New User" flow.

## Design decisions

**Token stored before `getAgents` call (cloud).** The sequence in `onSuccess`: `login(userId, token)` (which triggers Zustand persist → localStorage) → `api.getAgents()` (which reads the token from localStorage via `getAuthHeaders`). Same reasoning as before — see commit `b4b58ce`.

**Cloud branch is an entirely separate JSX subtree.** Rather than conditional rendering of individual fields, the two modes render completely separate `<div className="space-y-5 mt-6">` trees. This makes each branch independently readable and avoids shared state leaking between modes.

**`handleLocalLogin` replaces the old `handleLogin`.** The cloud login path is now fully owned by `useNetmindAuth`; the old unified `handleLogin` was split to avoid dead code in cloud mode. `handleLocalLogin` is only reachable in local mode.

**"Change Mode" button is hidden for `cloud-web` mode.** Force-deployed cloud builds should not offer users a way to switch to local mode. The button is only shown when `mode !== 'cloud-web'`.

**`handleChangeMode` clears `cloudApiUrl` before resetting mode.** Clearing the URL prevents the next cloud mode selection from silently reusing the old server URL without prompting the user.

**Post-login `?next=` return path (open-redirect guarded).** `ProtectedRoute` sends unauthenticated visitors to `/login?next=<encoded-path>`; after auth, login reads `next` from `location.search` and navigates there via `navigate(isSafeReturnTo(next) ? next : '/')`. The guard (`lib/safe-return`) accepts only same-origin relative paths, so a crafted `?next=https://evil.com` falls through to `/` instead of redirecting off-site.

## Gotchas

**`PublicRoute` redirects to `/mode-select` if `mode` is null.** If a user's localStorage was cleared (e.g., via DevTools or a `localStorage.clear()` call in Tauri mode-switch logic), they will be redirected from `/login` to `/mode-select` even if they navigate directly. This is correct behavior but can be surprising during testing.

**`CreateUserDialog` auto-fills the login field via `onCreated(userId)`.** After successful user creation, the dialog calls `onCreated` which sets the `userId` state in `LoginPage`. The user still needs to click "Access Terminal" manually — there is no auto-submit.
