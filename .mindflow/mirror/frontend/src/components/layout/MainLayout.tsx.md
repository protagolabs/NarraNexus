---
code_file: frontend/src/components/layout/MainLayout.tsx
last_verified: 2026-05-08
stub: false
---

## v2.3 改动（2026-05-08）

- **4-column layout**: `ChatView` now renders `<ArtifactColumn agentId={agentId} />` between the chat column and the context column. `ArtifactColumn` auto-hides when no artifacts are loaded, so the layout degrades gracefully to 3 columns for agents that don't produce artifacts.
- **WS lifecycle**: `ChatView` mounts a `useEffect` on `agentId` that calls `loadPinned(agentId)` and `connectWs(agentId)`. `disconnectWs()` is called on cleanup. This wires the artifact WebSocket channel to the currently active agent.
- **Session-ID gap**: `chatStore` does not expose a per-agent session ID (`AgentChatState` has no `sessionId` field). `loadForSession` is intentionally not called — session-scoped artifacts arrive via the `artifact.created` / `artifact.updated` WS events as the agent runs. If a session-ID source is added to `chatStore` in the future, add a `loadForSession(agentId, sessionId)` call here.
- **ASCII diagram updated**: file header now shows 4 columns.

## v2.2 改动（2026-04-13）

- **G1 内层 Suspense**：`<Outlet />` 包了 `<Suspense fallback={<DashboardSkeleton />}>`。理由：App.tsx 外层 Suspense 一旦触发会全屏覆盖（包括 Sidebar），用户感受是"点击后整页消失"。内层 Suspense 把 fallback 限在主内容区，Sidebar 始终可见 → 慢导航问题修复。
- DashboardSkeleton 的形状刻意 mimic 真实 dashboard grid，避免 swap 时跳行。

# MainLayout.tsx — Four-column app shell and React Router layout

## 为什么存在

React Router renders this as the layout wrapper for all `/app/*` routes. It decides whether to show the default `ChatView` (chat + artifact column + right panel) or delegate to `<Outlet />` for sub-pages like Settings and System.

## 上下游关系
- **被谁用**: React Router route config (`/app` layout route).
- **依赖谁**: `Sidebar`, `ChatPanel`, `ArtifactColumn`, `ContextPanelHeader`, `ContextPanelContent`, `AgentCompletionToast`, `usePreloadStore`, `useConfigStore`, `useArtifactStore`, `useAutoRefresh`.

## 设计决策

`isSubPage` is detected by checking `location.pathname` — if not `/app/chat` or `/app`, the `<Outlet />` gets the full remaining width with no right panel. This avoids needing a nested router layout for each sub-page.

`ChatView` is a separate named export (not inline) so it can be referenced without instantiating `MainLayout`. It owns the `ContextTab` state — the right-panel tab selection does not persist across navigation.

`preloadAll` is called whenever `agentId` or `userId` changes. This is the entry-point for populating `usePreloadStore` with awareness, social network, inbox, and other agent-specific data.

The artifact WS lifecycle (`connectWs` / `disconnectWs`) lives in `ChatView`'s `useEffect(_, [agentId])`. This ensures the artifact stream tracks the currently selected agent and is torn down on agent switch or unmount.

## Gotcha / 边界情况

`onAgentComplete` is passed to `ChatPanel` as a callback that calls `refreshAll` from `useAutoRefresh`. This is the mechanism that refreshes the right-panel data after an agent run finishes.

**Right-panel height chain (must keep `flex flex-col` on the framed wrapper)**: the `<div>` that wraps `<ContextPanelContent>` carries both `overflow-hidden` (visual clipping for the bordered frame) and `flex flex-col` (so the chain flex-1 → ContextPanelContent's flex-1 → Card's h-full → CardContent's overflow-y-auto can resolve a real height). Removing `flex flex-col` breaks the chain — every right-side panel renders at content height, gets clipped, and the user sees a "tab won't scroll" bug. There is no React state hint in this file telling future editors that the className is load-bearing; this paragraph is the warning.

**ArtifactColumn conditional rendering**: `{agentId && <ArtifactColumn agentId={agentId} />}` — when `agentId` is falsy (no agent selected), the column is omitted entirely. Inside `ArtifactColumn`, if `artifacts.length === 0`, it also returns `null`. The net result: the column only occupies layout space when there is both an active agent AND at least one artifact loaded.
