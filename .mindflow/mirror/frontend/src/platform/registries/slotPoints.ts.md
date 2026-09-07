---
code_file: frontend/src/platform/registries/slotPoints.ts
last_verified: 2026-09-07
stub: false
---

# registries/slotPoints.ts — slot points and content registries

## Intent

Where a plugin adds UI *inside* surfaces the shell already draws: seven slot points (`conversationKinds`, `chatHeaderActions`, `composerExtensions`, `messageActions`, `sidebarSections`, `agentCardBadges`, `topBarItems`) plus the two content registries (`messageRenderers`: own the bubble of a message you recognise; `timelineEvents`: render a timeline event type the shell does not know). Component slots take `{component, when, order}`, action slots `{label, run(ctx), when, order}`; the `validated()` wrapper parses `when` at registration. `visibleSlotEntries` (filter + sort) and `rendererFor` (lowest-order match, a throwing matcher counts as no match) are the two host-side reads. The shell registers `chat` as a conversation kind, builtin.teams `team`.

## 2026-09-07 — `validated()` uses `Registry`'s `validate` option (M-11)

`validated()` used to overwrite the returned instance's OWN `register` property
(`registry.register = (id, value, options) => {...}`) — one of two functionally-identical
"validating registry" patterns in this codebase (`themes.ts` subclassed `Registry` instead). Both
now use `Registry`'s constructor-level `validate` option (see `registry.ts`'s mirror doc);
`validated()` is now just `new Registry<T>(kind, { validate: (value) => parseWhen(value.when) })`.
