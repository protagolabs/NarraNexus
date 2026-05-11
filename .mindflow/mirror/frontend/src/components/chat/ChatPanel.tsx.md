---
code_file: frontend/src/components/chat/ChatPanel.tsx
last_verified: 2026-05-08
stub: false
---

# ChatPanel.tsx — Unified timeline chat surface with streaming and history pagination

## 为什么存在

The primary user-facing interface. All agent interaction goes through here. Merges two data sources (DB history and live WebSocket session) into a single chronologically ordered `TimelineItem[]` so the user sees one seamless conversation regardless of how many messages have been paginated or how the current run is progressing.

## 上下游关系
- **被谁用**: `MainLayout.ChatView`.
- **依赖谁**: `MessageBubble`, `EmbeddingBanner`, `useChatStore`, `useConfigStore`, `useAgentWebSocket`, `api.getSimpleChatHistory`.

## 设计决策

**Unified timeline**: History messages and session messages are merged and sorted by timestamp. Dedup is done by `role:content` key + 60-second timestamp-proximity check. **Match-and-consume semantics (Bug 19 fix)**: once a session message pairs with a history timestamp, that timestamp is spliced out of the per-key array so it can't dedup another session message with the same role+content. Without consumption, a single history row would gobble multiple session messages — realistic trigger is "user retries the exact same question after a failed turn", which would silently drop the retry bubble from the UI. Plan B (event_id-based precise dedup) is logged in `reference/self_notebook/todo/waiting/chat_dedup_by_event_id.md` as a future upgrade.

**Polling**: A 12-second interval polls for new background messages (from non-chat agent runs like Jobs). It only replaces the tail of history to avoid losing scroll position for users who've loaded older messages.

**Auto-load when not scrollable**: If the initial history page doesn't fill the container, the panel automatically calls `loadMoreHistory` until the container is scrollable. This prevents the "infinite scroll trigger never fires" problem when messages are small.

**IME handling**: The send button is gated by `isComposing` and a 100ms grace period after `compositionend`. Without this, CJK input methods would fire Enter before the character is committed.

**Bootstrap greeting**: If `bootstrap_active` is true and there are no messages, the panel renders a hard-coded bootstrap greeting. The greeting content is kept in sync with `src/xyz_agent_context/bootstrap/template.py` — comment in the code flags this dependency.

**`send_message_to_user_directly` filtering**: Tool calls with this name are filtered out of the streaming step preview — they produce the main message content, not a tool activity row.

## Gotcha / 边界情况

`flushSync` is used when prepending older messages after "load more" — this forces React to update the DOM synchronously before the scroll position is restored. Without `flushSync`, the scroll restoration would measure the old `scrollHeight`.

The `shouldAutoScrollRef` is the gating mechanism for scroll behavior. User scrolling up disables auto-scroll; new messages re-enable it; streaming start re-enables it.

**Two-mode scroll (Bug 15)**: scroll-to-bottom is split into two effects because "initial open" and "streaming tick" have incompatible requirements. `initialScrollPendingRef` is raised whenever fresh content arrives (initial load, agent switch, background poll, user's own submitted message). A dedicated effect picks it up, waits one `requestAnimationFrame` so `MessageBubble` subtrees (markdown, code blocks, tool-call UI) get a frame to lay out, then snaps `container.scrollTop = container.scrollHeight` — instant, not smooth, and scoped to `scrollContainerRef` only (scrollIntoView on a sentinel would also scroll ancestor containers). The streaming effect uses the classic smooth `scrollIntoView` + sentinel, gated by `isStreaming`, because during streaming the deltas are small and smooth feels right. If you ever need to "jump to bottom" from a new code path, set `initialScrollPendingRef.current = true` — do NOT reach for `scrollIntoView` directly (smooth loses the race against async content layout; that was the Bug 15 root cause).

## v2.4 改动（2026-05-08）— Inline artifact preview cards

- **`ArtifactToolCallCards` component**: a file-local component that receives `toolCalls: AgentToolCall[]`, `agentId`, and `allArtifacts` (pre-read from the store at component scope — not inside the map callback). For each tool call where `tool_name ∈ {create_artifact, upload_artifact_file}` and `tool_output` parses as JSON with an `artifact_id`, it renders an `ArtifactPreviewCard`. While the artifact is not yet in the store, it shows a "Loading artifact…" placeholder and fires `ensureArtifactLoaded` (fire-and-forget fetch → upsert).
- **`ensureArtifactLoaded` helper**: a module-level function (not a hook) that checks `useArtifactStore.getState().artifacts` for the given `artifact_id`. If absent, calls `artifactsApi.getDetail` and upserts the result. Safe to call on every render because the store lookup short-circuits immediately when already cached.
- **Hook rule compliance**: `allArtifacts` is read via `useArtifactStore((s) => s.artifacts)` at the `ChatPanel` component scope (top-level hook call), then passed down as a prop. This avoids calling a hook inside the `timeline.map()` callback.
- **Placement**: `ArtifactToolCallCards` is rendered as a sibling of `MessageBubble` inside each timeline item's wrapper `<div>`, so the cards appear below the message bubble.

## 新人易踩的坑

`BOOTSTRAP_GREETING` must be kept in sync with the Python backend constant. It's a frontend-only rendering shortcut — the greeting is never actually stored as a chat message until the user replies.

**Artifact preview placement**: the `ArtifactToolCallCards` render is gated by `hasArtifactTools`, which checks `item.role === 'assistant'`, `agentId` being truthy, and at least one qualifying tool call. This prevents the component from mounting on user messages or when `agentId` is not yet set. The `allArtifacts` dependency means the cards re-render when the store updates (e.g., after `ensureArtifactLoaded` upserts the fetched artifact), replacing the placeholder with the real card automatically.
