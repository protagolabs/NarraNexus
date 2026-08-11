---
code_file: src/xyz_agent_context/schema/team_schema.py
last_verified: 2026-08-10
stub: false
---
## 2026-08-10 — Team 增加 patrol_enabled / last_patrol_at(只读)

Leader 巡查的两列。**`patrol_enabled` 为 NULL 时,对*有 lead* 的 team 读作
开** —— 设 lead 这个动作本身就是在说「这个负责」,不必再问一遍;没有 lead 的
team 永不巡查,平台不替用户指定负责人。这条规则不显然,而它此前只活在代码注释里。

两列都**不写进 `_entity_to_row`**:归 [[patrol]] 与巡查开关端点所有。走 team
CRUD 的话,一次无关的改名就会把巡查游标清掉。

# team_schema.py — Pydantic 模型 for subproject 1

定义 `Team`, `TeamMember`, plus API request/response 模型 (`CreateTeamRequest`, `UpdateTeamRequest`, `TeamWithMembers`, ...)。

`Team.source` 字段语义：
- `"user"` — 用户在 UI 创建
- `"bundle"` — 从 .nxbundle import 进来（bundle.importer 设的）

`Team.intro_md` 字段是议题 8 的 onboarding（README.md）落地点。

## 2026-07-21 — Team.lead_agent_id

`Team` + `UpdateTeamRequest` gained `lead_agent_id` — the default responder for a team-chat
message with no @mention (None = earliest-joined member). See [[teams]].

## 2026-08-10 — `patrol_is_on` 搬到这里

规则读的两个字段(`lead_agent_id` / `patrol_enabled`)都是 Team 的字段,所以规则
归 Team。它此前住在 `team_work_schema.py`,只因为巡查是当时唯一的调用方 —— 结果
是一条 Team 规则藏在工作项模块里,看 Team 的人找不到。

规则本身容易说、也容易说错:`patrol_enabled` 对所有早于该列的 team 是 NULL,
NULL 读作**开**;但仅限**有 lead** 的 team —— 设 lead 才是「指定了负责人」这个
动作,没有 lead 的 team 永不巡查,平台不替用户指派。
