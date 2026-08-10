---
code_file: src/xyz_agent_context/repository/channel_trigger_audit_repository.py
stub: false
last_verified: 2026-05-08
---

## 2026-08-10 — recent_for_agent + SQL 下推 + 归一器迁出

`recent_for_agent`(static:实例按 channel 构造,而 agent 轨迹跨
channel——诊断端点的查询形状);`recent()` 从全表拉取+Python 排序改
SQL order/limit;`count_by_type` 加列投影(不再拖整窗 details)。
`_event_time_str` 迁至 utils/db/dialect_time 公有(留兼容别名)——
曾在本仓库与 Lark 仓库各一份拷贝、且被路由跨包 import 私名。

## Why it exists

Generic multi-channel version of ``LarkTriggerAuditRepository``. The
trigger runs in its own EC2 container, where pulling logs out post-
incident is hard. This repository is the trigger's black-box recorder
— every interesting lifecycle event lands in one row.

Phase 1 ships this alongside the existing Lark-specific repo (no
behavioural change to Lark). Phase 2 will redirect Lark writes here
and drop the old repo.

## Design decisions

- **Best-effort writes that NEVER raise.** ``append`` swallows every
  exception and logs to loguru. Losing an audit row is always
  preferable to stalling real user traffic.
- **JSON ``details`` column.** Adding new fields to an event type
  doesn't require a migration — just stash into ``details`` and
  the new field flows into the JSON blob.
- **Per-channel cleanup + filtering.** Every query / cleanup adds
  ``channel = self._channel`` so one channel's bursty volume doesn't
  swamp another's queries.
- **String constants, re-exported.** Event types live in
  ``xyz_agent_context.channel.channel_audit_events``. We re-export
  the common ones from this module so callers don't need a second
  import — same UX as the Lark version.

## Upstream / downstream

- **Upstream**: ``ChannelTriggerBase._audit`` (the only writer).
- **Downstream**: ``channel_trigger_audit`` table; consumed by future
  /healthz endpoints and admin UIs.
