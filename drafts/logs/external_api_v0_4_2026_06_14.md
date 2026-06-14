---
date: 2026-06-14
topic: external-api v0.4 implementation
status: done
branch: feat/external-api-v0.4
---

# External API v0.4 — implementation log

## Trigger
> "OK" → "rebase后create一个新branch,这里面继续开发,按你的phase来,全做完告诉我"

User confirmed v0.4 plan (see `[[2026-06-14 NarraNexus 外部接入协议 v0.4 plan]]` in obsidian) and asked to execute end-to-end on a new branch, interrupting only for important issues.

## Branch / commits

`feat/external-api-v0.4` (rebased onto `origin/dev` at 323da98e, 17 commits replayed from v0.3 + 3 new v0.4 commits).

- 1e72ca8c (carried over from v0.3) — last v0.3 fix
- 7b78acf7 — v0.4 Phase 2: RuntimePolicy + ExternalAgentRuntime skeleton + plumbing
- ff5fbd77 — v0.4 Phase 3: apply policy at all consumer sites + 30 unit tests
- (this log + final docs: pending commit)

## What got built

**RuntimePolicy dataclass + EXTERNAL_API_POLICY const** (`src/xyz_agent_context/agent_runtime/runtime_policy.py`):
- `skipped_modules` — modules not instantiated
- `mcp_denylist` — MCP URLs hidden from LLM (module + hooks still run)
- `extra_disallowed_tools` — appended to ClaudeAgentSDK's `disallowed_tools`
- `awareness_writable` — defense-in-depth (currently no-op since update_awareness is in mcp_denylist; reserved for non-MCP mutation paths)
- `memory_scope` — `"agent"` (current) or `"user"` (per-visitor)
- `identity_block_mode` — `owner` / `visitor` / `off`
- `hook_denylist` — placeholder for v0.5

**ExternalAgentRuntime subclass** (`src/xyz_agent_context/agent_runtime/external_agent_runtime.py`):
- Only override is `self._policy = policy` in __init__
- Factory helper `make_external_runtime_factory()` for BackgroundRun

**Plumbing** (additive, no behavior change for main runtime):
- `AgentRuntime.__init__` adds `self._policy = None`
- `AgentRuntime.run()` sets `ctx.policy = self._policy`
- `RunContext.policy: Optional[RuntimePolicy]`
- `step_0_initialize` passes ctx.policy to ModuleService
- `ModuleService` filters MODULE_MAP by skipped_modules + propagates policy to ModuleLoader
- `ModuleLoader` passes policy into every `module_class(...)` instantiation
- `XYZBaseModule.__init__` accepts `policy: Optional[Any] = None` as self._policy
- 7 modules (Awareness, BasicInfo, Chat, CommonTools, GeneralMemory, Job, Skill, SocialNetwork) **accept `**kwargs`** and forward to super (so policy reaches base class)
- `BackgroundRun` accepts `runtime_factory` kwarg; default = `AgentRuntime()`
- `backend/routes/external_api.py` passes `runtime_factory=make_external_runtime_factory()`

**Policy enforcement** at consumer sites:
- `memory/coordinator.py` — remember() + grep_memory() take scope_type/scope_id and pass to engine
- `general_memory_module.py` — `_retain_scope()` + `_user_scope_kwargs()` consult policy
- `basic_info_module.py` — visitor-mode override of `is_creator` / `user_role` / `current_speaker_name`
- `step_3_agent_loop.py` — mcp_urls filtering by policy.mcp_denylist + forwards extra_disallowed_tools to driver
- `agent_framework/xyz_claude_agent_sdk.py` — appends extra_disallowed_tools to ClaudeAgentOptions
- `agent_framework/agent_loop_driver.py` (Protocol) — adds extra_disallowed_tools kwarg to driver contract

## Key decisions made during execution

**1. WorkspaceModule doesn't exist as a module.** Audit found "workspace" is a per-agent directory, not a NarraNexus Module. The Claude Code SDK built-ins (Write/Edit/Bash) are what would let a visitor mutate files — `extra_disallowed_tools` handles this at the SDK layer.

**2. GeneralMemoryModule MCP tools can't enforce per-user scope.** The MCP tools take `agent_id` as a parameter and run in a separate process; they can't know which user is calling. Solved by suppressing the MCP URL for external sessions — the in-process hook auto-recall still runs with user scope, so the agent has memory; it just can't explicitly cross-search via `remember`/`grep_memory` tools.

**3. Identity block was moved post-Phase 0.3.** Commit `4da0d39a` on `origin/dev` (which Phase 0.3 didn't see) had REMOVED the User Identity block from ContextRuntime and moved identity rendering into BasicInfoModule. This was actually CLEANER for us — basic_info is a normal Module that reads `self._policy`, no need to pass policy through ContextRuntime.

**4. Phase 0.1 turned out to be a no-op.** Audit said add `users.owned_by_agent` to `_EXCLUDE` in identity_migration.py. But that column name isn't in `_IDENTITY_COLUMN_NAMES` set, so it's never scanned, never raises. Verified by running classify_identity_columns() — clean.

## Tests
- 30 new unit tests in `tests/runtime/test_runtime_policy_v04.py` — all pass
- 124 tests across `tests/agent_runtime` + `tests/module` + `tests/runtime` — no regressions
- 1 pre-existing failure in `test_attachments_audio_upload` (401 vs 200) confirmed unrelated by running on Phase 2 commit
- ruff clean on src + backend

## Docs updated
- `docs/external-api.md` — added "Per-session runtime restrictions (v0.4)" subsection in §2.4
- `[[2026-06-11 NarraNexus 外部接入协议 接口文档(给 gls)]]` (Chinese contract) — §7.3 rewritten from "待 fix" table to "✅ 已修" table with per-leak landing site
- `[[2026-06-11 NarraNexus External API Protocol — Contract (for gls) EN]]` (English contract) — same edit
- Mirror md synced for: runtime_policy (new), external_agent_runtime (new), agent_runtime, background_run, base, module_service, general_memory_module, basic_info_module, memory/coordinator
- `[[2026-06-14 NarraNexus 外部接入协议 v0.4 plan]]` — flipped status from "ready-to-execute" to "DELIVERED"

## Source-of-truth references
- Plan with phase-by-phase design: `Arena 客服接入/2026-06-14 NarraNexus 外部接入协议 v0.4 plan.md`
- Branch tree: <https://github.com/protagolabs/NarraNexus/tree/feat/external-api-v0.4>
- Phase 2 commit: 7b78acf7
- Phase 3 commit: ff5fbd77

## Next step
- Send updated Chinese / EN contract docs to gls (note §7.3 now ✅) — pending user push
- v0.5 candidates noted in plan: SCOPE_USER全量化 (DEFAULT_POLICY.memory_scope="user")、Manyfold mode policy variant、per-session workspace sandbox、hook_denylist 落实 (current value is empty placeholder)
