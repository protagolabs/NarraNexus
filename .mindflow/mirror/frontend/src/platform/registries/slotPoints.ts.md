---
code_file: frontend/src/platform/registries/slotPoints.ts
last_verified: 2026-09-04
stub: false
---

# registries/slotPoints.ts — slot points and content registries

## Intent

Where a plugin adds UI *inside* surfaces the shell already draws: seven slot points (`conversationKinds`, `chatHeaderActions`, `composerExtensions`, `messageActions`, `sidebarSections`, `agentCardBadges`, `topBarItems`) plus the two content registries (`messageRenderers`: own the bubble of a message you recognise; `timelineEvents`: render a timeline event type the shell does not know). Component slots take `{component, when, order}`, action slots `{label, run(ctx), when, order}`; the `validated()` wrapper parses `when` at registration. `visibleSlotEntries` (filter + sort) and `rendererFor` (lowest-order match, a throwing matcher counts as no match) are the two host-side reads. The shell registers `chat` as a conversation kind, builtin.teams `team`.
