---
code_file: backend/routes/_dashboard_schema.py
last_verified: 2026-04-13
stub: false
---

# backend/routes/_dashboard_schema.py — Intent

## 为什么存在
Pydantic 响应类型的**唯一真相源**（SSOT）for `GET /api/dashboard/agents-status`。

关键职责：**用类型系统把权限边界焊死**——owner-only 字段不能出现在 public 变体上，由 Pydantic `extra='forbid'` + `Literal[True/False]` discriminated union 强制。即使 `to_response` factory 写漏，validation 层拒绝序列化。

## 上下游
- **上游**：`backend/routes/_dashboard_helpers.py::to_response`（factory）、`backend/routes/dashboard.py` 路由响应
- **下游**：前端 `frontend/src/types/api.ts` 里手工复刻了同样的类型（TS 侧，Pydantic → TS 没自动化生成；drift 风险见 Gotcha）
- **平行**：`frontend/src/types/api.ts` 的 `OwnedAgentStatus / PublicAgentStatus` 必须和这里**字段-by-字段**对齐

## 设计决策
1. **Discriminated union via `owned_by_viewer`**：`Literal[True]` 和 `Literal[False]` 才是真 discriminator；普通 `bool` 字段 + 默认值在 Pydantic v2 + FastAPI response_model 里不 work。序列化出错会很隐蔽，所以必须 Literal。
2. **`ConfigDict(extra='forbid')` on PublicAgentStatus**：防御性措施。Factory 里写 `sessions=[]` 传给 public 会在 validation 就 raise，而不是序列化成功然后泄漏。
3. **`running_count_bucket` 替代精确数字** on public 变体（TDR-13）：侧信道防御，防止通过流量分析识别大客户 agent。
4. **v2.1 新增的 owner-only 字段**都在 `OwnedAgentStatus` 内：`verb_line / queue / recent_events / metrics_today / attention_banners / health`。Public 变体**刻意不含**这些。
5. **`action_line: str | None`** 而不是空串——`null` 让前端能明确渲染 `—`。

## Gotcha
- **TS 类型手工复刻**：后端加字段若忘了同步 `frontend/src/types/api.ts`，`tsc` 不报错（TS 对多余字段宽容）。没有自动化契约测试。加字段 checklist：改这里 → 改 types/api.ts → 跑 tsc。
- `running_count_bucket` 的字面值列表（`'0' | '1-2' | '3-5' | '6-10' | '10+'`）和 `_dashboard_helpers.py::bucket_count` 的输出是**隐式耦合**——改其中一个必须改另一个。
- `JobQueueStatus` 的 5 个值和 `_dashboard_helpers._LIVE_JOB_STATES`（去掉 `running`）**必须对齐**——改枚举两处都要动。
- 字段添加到 `OwnedAgentStatus` 时若是 owner-only：务必用 `ConfigDict(extra='forbid')` 保护 `PublicAgentStatus`（已做），并在 `tests/backend/test_dashboard_v21.py::test_v21_public_variant_still_locked_down` 的 `forbidden` 集合里加上新字段名，否则白名单漏检。
