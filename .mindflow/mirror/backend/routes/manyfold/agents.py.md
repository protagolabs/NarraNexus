---
code_file: backend/routes/manyfold/agents.py
last_verified: 2026-08-14
stub: false
---

## 2026-08-14 — create now materializes the workspace DIRECTORY (Manyfold #832)

`POST /manyfold/agents` used to write the `users` / `agents` rows and return
200 having **never created a directory**. `GET .../files/roots` then resolved
and returned that path anyway (`resolve_existing_workspace` falls back to the
current-layout default when no candidate exists), so the platform stored a
**path to nothing** and its runner — which calls `workspace.ensure(create=false)`,
correctly refusing to invent directories inside another system's layout —
could not start the sandbox.

**Ownership call**: the path is defined by NarraNexus (the layout has already
changed once under the platform, see [[workspace_paths]]), so **materializing
it is ours too**. The fix belongs in the create contract — not in the runner
(flipping it to `create=true` would hand the platform the right to create
arbitrary framework paths) and not in `roots` (a read endpoint must not have
side effects, and mkdir/mv/rm are deliberately unexposed on this gateway, see
[[files.py]]).

Three semantics worth keeping:

- **Exists before we answer**: `_ensure_workspace` delegates to
  [[workspace_paths]]`.ensure_agent_workspace` (via `asyncio.to_thread` — mkdir
  is blocking I/O). Failure → **500, never a false success**. A false success
  is a delayed detonation: it resurfaces minutes later on the platform side as
  a sandbox error that looks unrelated to agent creation.
- **Runs on the update leg too**: idempotent replay IS the repair channel — a
  workspace deleted out from under us comes back on the next addAgent
  (`agent_created=false`, directory restored).
- **No rollback**: a materialization failure does not delete the rows already
  written. Replay repairs, whereas deleting rows would destroy the data of an
  agent that **already existed** (on the update leg those rows are usually not
  ours to remove).

Review found that filesystem idempotence alone was insufficient: the route's
user and agent paths were both read-then-insert sequences. Two overlapping
creates could therefore both observe a missing row and let one request crash
on the unique index. The create seam now treats the database constraint as the
arbiter: try the insert, classify only backend-specific duplicate-key errors,
then re-read the winning row. This keeps two same-agent creates and two agents
for one user idempotent without relying on a process-local lock (which would
not cover multiple workers).

`agent_id` is also an immutable ownership claim at this seam. A replay may
update name/description only when the stored `created_by` matches the
normalized caller; another user gets 409 without owner disclosure. The check
runs once before user creation for the sequential case and again after an
insert collision for the race. This prevents a colliding request from moving
the DB row to another user and materializing a new empty workspace that hides
the original owner's populated directory. Provider cloning happens only after
the claim succeeds, so a losing cross-owner request cannot copy provider state
before returning 409.

Workspace `OSError` details remain in server logs, but the HTTP 500 is generic.
The raw exception includes absolute deployment paths and must not cross the
gateway boundary. User-id normalization and workspace-segment `ValueError`s
are both translated to 400 before any row or directory is created.

Two supporting changes:

- **Id validation moved ahead of the writes**: `agent_id` arrives verbatim in
  the body and becomes a **path segment** here, so `validate_workspace_segments`
  runs before any row is written (400). It was previously unvalidated; now that
  we mkdir with it, a `../x` value must be rejected before it lands in the DB —
  otherwise a 400 still leaves a half-provisioned agent behind. The `user_id`
  side was already sanitised by `_normalize_user_id`; this is defense in depth.
- **Response gained a `workspace` field**: hands back the path we just
  guaranteed so callers stop re-deriving the layout (the platform's
  `narraNexusSeedWorkspacePath` calls itself "a SEED, not an answer", and its
  listAgents comment says to read such a field if NarraNexus ever adds one).
  The platform parses loosely (`res.json<T>()`), so an added field breaks no
  existing adapter.

**Deliberately not done**: `DELETE /manyfold/agents/{id}` still does not remove
the workspace directory (it only cascades DB rows). Deleting files is
irreversible and asymmetrically riskier than creating them, so it stays a
separate decision; the leftover-orphan-dir behaviour is unchanged by this fix.

Tests: tests/backend/test_manyfold_workspace_materialize.py (first create /
concurrent same-agent and same-user creates / cross-owner conflict / second
same-user agent isolation / replay repair / non-leaking failure without false
success / unsafe ids / create→roots round trip).

## 2026-07-23 — 收口第 4 条 agents 写路径的长度上限(review #2)

`ManyfoldCreateAgentRequest` / `ManyfoldUpdateAgentRequest` 的 agent_name /
description 改为 `Field(max_length=AGENT_TEXT_MAX_LENGTH)`(常量来自 entity_schema)。
这两个模型走 raw `db.insert` / `db.update("agents", ...)`,绕过 Agent 模型;之前
Create 完全不限长、Update 的 description 限 2000——2000 > 255 正是第 4 条能重造
#71 不可读行的洞。现与其余三处(读模型 / Create·UpdateAgentRequest / 导入修剪)
绑同一上限。

# manyfold/agents.py — Manyfold 网关的服务间集成路由

## 为什么存在

Manyfold 侧通过网关（`MANYFOLD_GATEWAY_TOKEN` 服务间密钥）在 NarraNexus 里
按需创建 `mf_*` 用户 + agent。仅 `ENABLE_MANYFOLD_API=1` 时注册（backend/main.py）。

## 2026-07-18 — 克隆走 cloud_policy 过滤（review 修复）

`_clone_provider_setup`（新用户从模板用户镜像 `user_providers` + `user_slots`）
过去用裸 `db.insert` 复制、完全绕过 netmind-only 门禁——code review 定为本次
策略的最大缺口：模板用户若持自有 key，新 mf 用户出生即带**已激活的非 NetMind
绑定**。修复：`netmind_slots_only(actor_is_staff=False)`（mf 用户恒为普通
用户）为真时只克隆 `source='netmind'` 的 provider 行，指向被过滤行的 slot
一并跳过（否则留下悬空且违规的引用）。本地不过滤。测试：
tests/backend/test_manyfold_provider_clone.py。

## 既有坑（未动）

- 目标用户已有同名 provider 时（name 去重跳过克隆），slot 克隆仍指向源用户的
  旧 provider_id → 可见性失败。边缘场景，先记录不修。
