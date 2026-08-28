---
code_file: frontend/src/pages/SetupPage.tsx
last_verified: 2026-08-28
stub: false
---
## 2026-08-28 — 订阅入折叠区首位,不自动跳转(P0:只配 subscription 走不通)

原布局把订阅埋在三层下(Advanced 折叠 → add modal → Sign in tab),主卡
OneKeyOnboard 又是纯 API key 表单,页脚 providerCount 只在挂载/折叠时
探测——订阅用户配完只看得到 "Skip for now",把 landing 读成"必须绑
API Key"。第一版做成与 one-key 并列的一等卡且连接即跳转;**Owner 手测
后否决**:点 Add 直接进产品太突兀,且要保留原页面格式。终版:

- 页面保持原格式(OneKeyOnboard 主卡 + 折叠区),但折叠区展开后
  **第一眼就是订阅卡**([[SubscriptionConnect]]),不再需要进 add modal。
  **仅 `mode === 'local'` 渲染**——云端不广告后端会 403 的路径(云端经
  RootRedirect 本就不进 /setup,这里防的是直接 URL 访问)。
- **订阅连接不触发导航**(不传 onConnected):本页瘦 `addProvider`
  (走 [[providersApi]])成功后 re-probe,页脚实时翻成 "Get Started",
  由用户自己离开。这是 Owner 明确的交互决策,别"顺手"改回自动跳转。
- `probe` 改 useCallback 并作为 `onProvidersChanged` 传给
  ProviderSettings——Advanced 开着时页脚也实时翻转,折叠时 re-probe
  保留为兜底。**probe 必须保持稳定引用**(见 ProviderSettings mirror
  的陷阱)。
- `providersVersion` state 在订阅卡每次成功 add 后 +1,作为
  `refreshToken` 传给 ProviderSettings——否则其自有的 "Your providers"
  网格不知道该刷新(Owner 走查第 2 轮发现)。
- 测试:`__tests__/setup-page-subscription.test.tsx`(折叠内 local
  渲染 / cloud 展开也不渲染 / 展开不导航)。

## 2026-08-04 — 根容器 h-screen → h-dvh-safe

整屏根改用 index.css 的 `.h-dvh-safe`（100vh 兜底 + 100dvh 覆盖），与
MainLayout/App.tsx 同批收口移动端视口高度问题。纯样式，无逻辑变化。
## 2026-07-28 — Beta badge in the header

The header logo now sits in a flex row with [[BetaBadge.tsx]] (shared brand
beta marker). Purely visual — no funnel/provider logic change.

## 2026-07-20 — localized first-run shell

The setup eyebrow, welcome heading, advanced disclosure, and completion CTA
now follow the active locale. Provider probing, funnel events, and navigation
semantics are unchanged.

## 2026-06-11 — merge: funnel events wired into the redesigned page

The dev-branch funnel instrumentation and the one-key redesign merged.
`finishSetup(event)` replaces both `goToChat` and dev's `handleDone`:
the footer "Get Started" button (providerCount > 0) and OneKeyOnboard's
`onComplete` fire `setup_completed`; the ghost "Skip for now" button
(providerCount === 0) fires `setup_skipped`; `setup_entered` fires once
on mount behind a StrictMode ref guard. Which event fires depends on
which button the user pressed, never on provider count alone.

## 2026-06-10 (later) — Get Started restored; OneKeyOnboard gained provider picker

Footer is provider-count-aware again: zero providers → ghost "Skip for
now"; any provider → accent "Get Started". Count re-probes when the
Advanced disclosure collapses (the user may have configured providers
inside it). The primary card now covers NetMind/Claude/OpenAI/Yunwu/
OpenRouter via the shared OneKeyOnboard.

## 2026-06-10 — one-key card is the primary first-run surface

SetupPage now renders `OneKeyOnboard` as the main path; the full
`ProviderSettings` moved behind an "Advanced setup" disclosure (collapsed by
default). The provider-count probe + Done/Skip dual-button logic is gone —
success navigates straight to /app/chat via onComplete; "Skip for now"
remains for users with no key.


# SetupPage.tsx — First-time LLM provider configuration wizard

## Why it exists

A new user who has just logged in cannot use the agent without at least one LLM provider configured. Rather than silently dropping them into the chat panel with cryptic errors, `RootRedirect` checks provider count on first load and routes to `/setup` if none are configured. This page is a guided onboarding step that can be skipped (if the user does not yet have API keys).

## Upstream / Downstream

Route: `/setup`, wrapped by `ProtectedRoute`. Entered automatically from `RootRedirect` when `providerCount === 0`, or revisited via direct URL.

On mount: calls `api.getProviders()` (authenticated, identity via auth header) to check current provider count, and fires the `setup_entered` funnel event. Uses the full `ApiClient` — the previous bare `getBaseUrl()` fetch that sent no identity headers was replaced so user identity travels correctly.

Composes `OneKeyOnboard` (primary) and `ProviderSettings` (behind the Advanced disclosure). Every exit path goes through `finishSetup(event)`: fires a funnel event and navigates to `/app/chat`.

## Design decisions

**Funnel instrumentation: fire-and-forget, never blocks navigation.**

Three funnel events are reported via the shared `captureProductEvent()` path:

- `setup_entered` — emitted once on mount via `useEffect([], [])`. Marks that
  the user reached setup.
- `setup_completed` — emitted by `finishSetup` from the footer "Get
  Started" button (shown when `providerCount > 0`) and from
  `OneKeyOnboard`'s `onComplete`.
- `setup_skipped` — emitted by `finishSetup` from the ghost "Skip for
  now" button (shown when `providerCount === 0`).

Capture is internally fire-and-forget — the funnel never blocks or errors the
user's navigation, while sharing session and idempotency semantics with the
rest of the browser lifecycle.

**"Skip for now" is visible only when `providerCount === 0`.** If providers are already configured (e.g., user navigated back to `/setup`), there is no skip option — only "Get Started". This prevents showing a skip button to users who have already done the setup.

**Provider count check is best-effort.** If the backend is unreachable, `providerCount` stays 0 (the catch block is silent) so the skip affordance remains. This avoids blocking login when the backend is momentarily unavailable.

**No back button.** Setup is a forward-only flow. To undo provider configuration, the user goes to Settings.

## Gotchas

**`providerCount` is re-probed on mount and when the Advanced disclosure collapses** — the user may have configured providers inside `ProviderSettings`, which flips the footer from "Skip for now" to "Get Started". It does not update live while the disclosure stays open.

**`setup_entered` fires even when the user revisits `/setup` after already configuring providers.** The `useEffect` has no condition on `providerCount`. This is correct — the event tracks "user reached this page", which is true on every visit.
