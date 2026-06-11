---
code_file: frontend/src/components/bookmarks/AgentProfilePanel.tsx
last_verified: 2026-06-10
stub: false
---

# AgentProfilePanel.tsx — "Who is this agent" drawer panel

## 为什么存在

Spec §3: Config (Awareness/Workspace/IM/Social), Skill & MCP and
Runtime-Narrative all describe the agent itself — setup-time identity,
not run-time activity. They merge behind the AGENT big bookmark as a
single-open accordion.

## 上下游关系

- **被谁用**: BookmarkDrawer children in MainLayout's ChatView.
- **依赖谁**: AwarenessPanel / SkillsPanel in `embedded` mode,
  NarrativeList (imported from its module directly, not the runtime
  barrel — RuntimePanel is being retired), [[bookmarkStore]].

## 设计决策

- **Single-open accordion** with default-open priority: explicit
  focusKey > section carrying a profile:* highlight > Awareness. The
  drawer lands on what changed (spec §6).
- AwarenessPanel's internal sections (Workspace / IM Channels / Social)
  stay inside it — this panel only orchestrates the top level.
- Highlight dot on a section header mirrors the small bookmark's
  yellow info dot; opening that section (click or mount-time focusKey)
  calls markOpened to clear it.
- Zustand selector picks the raw `agents[agentId]` slice; the `?? {}`
  fallback happens OUTSIDE the selector against a module-level
  constant — a fresh object per snapshot would loop
  useSyncExternalStore.

## 新人易踩的坑

Section ids ('awareness' | 'skills' | 'memory') are the contract with
bookmarkStore's `profile:<section>` keys — renaming one side breaks
default-open and highlight clearing silently.
