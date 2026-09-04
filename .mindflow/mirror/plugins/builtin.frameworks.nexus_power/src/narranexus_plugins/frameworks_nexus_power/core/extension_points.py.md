---
code_file: plugins/builtin.frameworks.nexus_power/src/narranexus_plugins/frameworks_nexus_power/core/extension_points.py
last_verified: 2026-09-04
stub: false
---

# nexus_power/extension_points.py — the loop's strategy seats as slots

## Intent

The third vertical grain of the pipeline (spec §484): platform stages → act-stage framework → seams inside the framework. Five NexusPower protocols become slots under the plugin's own namespace (`builtin.frameworks.nexus_power.{stop,compaction,projector,expression,policy}` — the manifest ownership rule requires the plugin id prefix, so the spec's short `nexus_power.stop` reads as this path). The loop's own classes (`NoMoreActionsStop`, `ToolResultPruner`, `PassthroughProjector`, `ExpressionContract`, the three policy layers in their historical check order) are the default providers; another plugin provides an alternative into the same slot and configuration binds it (`NX_BIND__builtin__frameworks__nexus_power__stop=<provider>`, comma list for the many-arity policy seat). Providing alone never rebinds a one-arity seat — binding is configuration, exactly as the slot tree promises.

A provider is `Callable[[SeatContext], impl]`; `SeatContext` carries the turn options, workspace, tool context, provider profile and (for the projector) the harness-inserted base messages + tail renderer. `resolve_one/resolve_many` apply env-over-default and fail loud on an unknown provider (`UnknownEntry`): a bound name nobody provides is a configuration error, never a silent fallback.

## Gotchas

- Registered twice on purpose: the manifest (booted hosts) and `ensure_registered` (any process running the loop without a plugin boot — the executor subprocess runner) — `Registry.register` and `HookCaller.add` are idempotent on same owner + name. Third-party seat providers therefore reach the loop only when their plugin is loaded in the process that runs it; the subprocess runner sees the defaults and env bindings.
- The kernel `bindings.resolve` needs every one-slot in the tree to have a default; the seats use a small local resolver over `parse_env` + `slot.default` for the same layering (env > default) without that global precondition.
