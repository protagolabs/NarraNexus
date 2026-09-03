---
code_file: frontend/src/components/bookmarks/BookmarkPanelHost.tsx
last_verified: 2026-09-03
stub: false
---

## 2026-09-03 — 挂载 `builder` 面板

`tab === 'builder'` → lazy 的 [[BuilderConfigPanel.tsx]]。与其他 panel 一样
lazy，所以不打开就不拉这块 chunk。

## 2026-08-19 — forceExpanded 随 sliver 一起退役(下方 08-06 条以本条为准)

`ArtifactColumn` 的 sliver/collapse 逻辑整体删除后,`forceExpanded` 只剩
这里一个恒 true 的调用点,prop 已删——现在直接
`<ArtifactColumn agentId={agentId} />`,可见性仍归 drawer 壳
(见 [[../artifacts/ArtifactColumn.tsx]] / [[../../stores/artifactStore.ts]]
08-19 条)。08-06 条里「forceExpanded:跳过其自身 sliver/collapse 逻辑」的
描述随之失效;「侧边 Artifact 栏随之退役」仍成立。

## 2026-08-06 — artifacts 面板

`artifacts` tab → lazy ArtifactColumn(forceExpanded:跳过其自身
sliver/collapse 逻辑,可见性归 drawer 壳)。侧边 Artifact 栏随之退役
(见 [[../layout/MainLayout.tsx]])。

# BookmarkPanelHost.tsx — One lazy panel per atomic tab

## 为什么存在

Renders the single panel behind an atomic tab. Every panel is
React.lazy'd (the retired ContextPanelContent pattern): clicking a tab
mounts exactly one light chunk — the direct fix for the "small tabs
respond slowly" feedback (the first drawer iteration statically
mounted Jobs+Inbox / a whole accordion).

## 设计决策

- awareness/workspace/channels/social → AwarenessPanel `section` prop;
  skills/mcp → SkillsPanel `section` prop — atomic rendering reuses the
  existing panels' state and logic, nothing forked.
- Mount-time markTabOpened clears the tab's info highlights.
- JobsPanel's onJobResolved wires into resolveJobAttention.

## 新人易踩的坑

ActivityPanel / AgentProfilePanel (the multi-section first iteration)
were deleted 2026-06-11 — don't resurrect stacked sections; Owner rule
is one tab = one content.
