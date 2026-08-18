---
code_file: backend/routes/agents/social_network.py
last_verified: 2026-08-17
stub: false
---

## 2026-08-17 — create-agent 归一名字/描述,并拒绝空名(与 DirectStore 同构)

`POST /{agent_id}/social-network/create-agent`:`normalize_agent_text` 名字与描述,
空名回 `CREATE_AGENT_EMPTY_NAME_MSG`(共享串)。补记(同日 review 第三轮):`CreateAgentBody.agent_name` 去掉了 `min_length=1`。
带着它,`agent_name=""` 会在路由自己的空名检查**之前**先 422,于是模型在云端
拿到的是 transport 降级串、在本地拿到共享常量 —— 同一次工具调用两句话,
而这个常量存在的全部理由就是 byte-parity。`"   "` 没这个问题(过 min_length,
再被归一成 `""` 命中检查),裂开的只有真空串这一支。
`max_length=128` 保持不变(与 `AGENT_TEXT_MAX_LENGTH=255` 不一致是既有问题,
不在本次范围)。测试:`tests/backend/test_create_agent_empty_name_parity.py`
—— 两条路径 × `""` / `"   "`,必须回同一个串。

理由与陷阱见 [[store.py]]
2026-08-17 条 —— 两边是 byte-parity 孪生,必须一起改。

连带一处:成功回执与日志改用**归一后**的 `agent_name`,不再是 `body.agent_name`。
否则回给 agent 的名字与 [[agent_repository]] 实际写进行里的不同形。

## 2026-08-11 — 三个 GET 读端点补 owner-only（安全审计 IDOR/P0-1）

写端点（POST）在 2026-08-10 PR-2 已加 `assert_owned`，但三个 GET 读端点
（`/search`、`/{user_id}`、根 `/social-network`）当时**漏了**——任何登录用户凭
`agent_id` 就能读任意 agent 的社交网络。现在三者都先 `await assert_owned(request, agent_id)`
（放在 try 之前，403/404 才不会被 except 吞成 200）。cloud 强制 owner、local no-op。
顺带把这三个 GET 及三个 POST 的 catch-all `error=str(e)` 收敛为通用文案（内部错脱敏）。

## 2026-08-10 (PR-6) — create-agent 路由接受调用方铸造的 new_agent_id

`CreateAgentBody` 加 `new_agent_id` 字段：路由不再自己 `uuid4` 生成，而是 provision
调用方（MCP 工具经 seam）传入的 id——使 DirectStore 与本路由用同一 id、输出逐字相同。
owner-gated；`new_agent_id` 用 `pattern=^agent_[0-9a-f]{12}$` 约束——它会成为
workspace 路径段，无约束的 `../victim/agent` 会跨租户写入他人工作区（Bootstrap.md
注入）。仅靠"重复 id 会失败"是**错的**（只防 DB 主键碰撞，不防路径穿越）。
[[provision]] 的 `provision_new_agent` 再用 `_SAFE_AGENT_ID` 兜底。
成功 dict / 无 owner 文案改用共享 `format_create_agent_success` / `CREATE_AGENT_NO_OWNER_MSG`
（与 DirectStore 同源）；异常改 `f"Error: {e}"` 与工具对齐（HttpStore 逆映射成 message）。
`uuid4` import 删除。

## 2026-08-10 (PR-5) — 新增 3 个读 seam-孪生端点（POST /recall /contact /stats）

READ MCP 工具（search/get_contact_info/get_agent_social_stats）的 byte-parity
HTTP 孪生。**POST 不 GET**：动作子路径避开既有 GET `/{user_id}` 路径参数冲突。
owner-gated。返回**工具 dict shape 原样**（message 键、**不** `_normalize_write_result`），
故 HttpStore 2xx 直接透传。contact/stats 用 [[social_network_module]] 的共享
`format_contact_result`/`format_stats_result` 整形（与 DirectStore 同源）。前端向的
旧 GET response_model 端点不动、各自独立。三个读路由整体包 try/except：实例解析 db
故障也返回 200 + 工具 message shape（`{success:False, message:"Error: ...", results:[]}`），
不吐 500——与 DirectStore reads 逐字对齐、守住 store docstring 的"handlers answer 200"契约。

## 2026-08-10 (PR-4) — 写端点成为 HttpStore 孪生 + 实例文案改共享源

三个写端点（extract/merge/delete）现是 AgentDataStore seam 的 HttpStore 目标。
`_resolve_social_instance_id` 的"无实例"文案改用 [[social_network_module]] 的共享
`social_instance_not_found_msg`（措辞不变），使 route 与 DirectStore 逐字同源；
route 侧失败仍走 `_normalize_write_result`(message→error)，HttpStore 端做精确逆
还原成工具 `message` 形状（见 [[store]]）。GET 端点里原本重复的那份同样字符串
本 PR 也一并收编到共享 `social_instance_not_found_msg`（route 内已零字面量）。

## 2026-08-10 (round-2/3) — 四个写端点全部委托,不再有手工同步复制

round-2 把此前逐字复制的业务逻辑全部提炼成可 import 的委托目标,下方
「Where the logic lives」那段(说 merge/delete/create-agent 是无法委托的
闭包复制品、drift 需手工同步)**已作废**:
- `merge` → `SocialNetworkModule.merge_entities`;`delete-entity` →
  `SocialNetworkModule.delete_entity`(闭包提炼为真方法,MCP 工具与路由同调);
- `create-agent` → [[provision]] `provision_new_agent`(agent 行+默认实例+
  发现注册+bootstrap+默认技能安装+awareness seed 的唯一序列;auth.py / MCP
  工具 / 本路由三处收敛)。「MCP 副本仍坏 + auth.py 待提炼」那条 todo 也已
  在本 PR 内做完,不再是后续项。
- create-agent 成功响应新增可选 `warnings` 字段(seam 收集的非致命供给告警,
  半供给 agent 的运维信号,incident lesson #5)——对外 API 形状变化。

## 2026-08-10 (pre-open review) — create-agent 走 canonical 三步供给

原实现复刻的 MCP 工具本身就是 auth.py 创建路径的不完整副本:缺
InstanceFactory.create_agent_level_instances(社交/基础/总线实例)、
sync_agent_discovery(同伴发现目录)、apply_bootstrap(profile 渲染的
Bootstrap.md+greeting+删除规则)——造出的 agent 对同主其他 agent 不可见
(正是 "ask agent X came back empty" P1)。路由现执行与 auth.py 相同的
三步,awareness 文本 seed 到工厂建出的实例上。(**后续 round-2 已收敛**:见
顶部 round-2/3 条目——三处调用方全部改调 `provision_new_agent`,MCP 副本与
auth.py 提炼都在本 PR 内完成,不再是 todo。)

## 2026-08-10 — write endpoints added (PR-2 · MCP data-access seam, backend half)

Added four POST endpoints (`extract` / `merge` / `delete-entity` / `create-agent`) that give an HTTP caller the same data-mutation power as the four write tools in `_social_mcp_tools.py` (`extract_entity_info`, `merge_entities`, `delete_entity`, `create_agent`). This is the non-agent-triggered path to the same social-network data — e.g. a frontend "merge duplicate contacts" button, or an admin cleanup script, without routing through an agent's own tool-call loop.

Each endpoint calls `assert_owned(request, agent_id)` first (403 non-owner / 404 unknown agent / 503 on a failed ownership lookup — see `backend/routes/_ownership.py`). This is the first ownership check this file has ever had; the three pre-existing GET endpoints below remain unauthenticated reads.

**Where the logic lives (superseded 2026-08-10 round-2 — see the entry at top):** ALL four write endpoints delegate now — `extract` →
`SocialNetworkModule.extract_and_update_entity_info`, `merge` →
`SocialNetworkModule.merge_entities`, `delete-entity` →
`SocialNetworkModule.delete_entity`, `create-agent` → `provision_new_agent`.
The MCP tool closures were extracted into those same targets, so the route
and the tool share one implementation and cannot drift. (The paragraph that
used to live here described the pre-refactor state where merge/delete/create
were hand-copied closures — no longer true.)

**Deliberate deviations from the MCP tool surface** (call these out if you're diffing behavior):
- `extract`'s `updates` field is typed as a JSON object in the request body (FastAPI/Pydantic enforces this at the boundary). The MCP tool additionally accepts a JSON-*string* for `updates` and parses it — that's an LLM tool-calling quirk (some models emit a stringified object), not a data-semantics difference, so the HTTP route doesn't replicate it.
- Failure payloads use this file's established `{"success": False, "error": ...}` shape (matching the three GET endpoints below), not the MCP tools' `{"success": False, "message": ...}` shape. `_normalize_write_result()` renames `message` → `error` on the way out of the `extract` call; `merge` / `delete-entity` / `create-agent` build their failure dicts with `error` directly since their logic is inlined here anyway.
- The "no SocialNetworkModule instance" error text matches this file's GET endpoints ("... for agent: {agent_id}") rather than the MCP tool's phrasing ("... for agent_id={agent_id}") — same reasoning, family consistency over verbatim match.
- `delete-entity` is POST, not HTTP DELETE, so the target `entity_id` can travel in a JSON body like the other three write endpoints (this route family doesn't use path/query params for write targets).

## 2026-06-08 — entity endpoints route through the repo

Both endpoints now go through `SocialNetworkRepository` (reading `memory_entity`) plus an `_entity_to_info` helper, instead of touching `instance_social_entities`. Behaviour for callers is unchanged; only the storage source moved.

# agents/social_network.py — 社交网络实体路由（读 + 写）

## 为什么存在

`SocialNetworkModule` 维护 Agent 认识的人/组织的档案，存储在 `instance_social_entities` 表。这个路由暴露三个只读接口（查询单个实体、列出所有实体、关键词/语义搜索）加四个写接口（extract/merge/delete-entity/create-agent）。只读接口服务于前端的社交网络面板和调试；写接口是 `_social_mcp_tools.py` 里同名 MCP 工具的 HTTP 镜像，给非 agent 调用方（前端按钮、管理脚本）一条不经过 agent 工具调用循环就能读写同一份社交网络数据的路径。

## 上下游关系

- **被谁用**：`backend/routes/agents/core.py` 聚合；前端社交网络面板；写端点面向前端管理操作/脚本调用（不经过 agent 的 MCP 工具调用）
- **依赖谁**：
  - `InstanceRepository` — 查询 `SocialNetworkModule` 实例 ID
  - `SocialNetworkRepository` — 读端点的语义搜索（`semantic_search`）、关键词搜索（`keyword_search`）、单实体查询（`get_entity`）；写端点的实体改删逻辑已下沉到 `SocialNetworkModule.merge_entities`/`.delete_entity`，路由不再直接调 repo 的写方法
  - `SocialNetworkModule.extract_and_update_entity_info` — `extract` 端点直接委托给它，保证与 agent 工具路径语义一致
  - `SocialNetworkModule.merge_entities` / `.delete_entity` — `merge` /
    `delete-entity` 端点委托的真方法(与 MCP 工具同源)
  - `AgentRepository` — `create-agent` 端点解析 creator 的 owner
  - `xyz_agent_context.bootstrap.provision.provision_new_agent` —
    `create-agent` 的唯一供给入口([[provision]];建 row/实例/发现/bootstrap/
    默认技能/awareness seed 全在 seam 内,本路由不再直接建 row 或 seed)
  - `backend.routes._ownership.assert_owned` — 四个写端点的授权门禁
  - （历史：语义搜索曾经由 agent_framework 的 embedding 工具生成 query 向量；该向量化子系统已整体移除）
  - `xyz_agent_context.utils.db.db_factory.get_db_client` — 直接查询 `instance_social_entities` 表

## 设计决策

**路由注册顺序**

`/{agent_id}/social-network/search` 必须在 `/{agent_id}/social-network/{user_id}` 之前注册，否则路径匹配时 "search" 会被当成 user_id 的字符串值，把搜索请求路由到单实体查询接口，导致查不到结果但也不报错。注释里专门标注了这个要求。FastAPI 在同一路由器内按注册顺序匹配，不按路径特异性排序。

**硬限 1000 条**

`get_all_social_network_entities` 用 `limit=1000` 硬限制最大返回数量。对于正常使用场景（Agent 通过日常对话积累的社交关系，通常是几十到几百条）这够用，但如果一个 Agent 接入了大型通讯录，1000 条可能不够。目前没有分页接口。

**语义搜索即时 embedding**

搜索时调用 `get_embedding(query)` 实时生成向量，这会产生一次 LLM API 调用（embedding 接口）。如果 embedding 服务不可用，语义搜索会抛异常，前端需要处理。关键词搜索不依赖外部服务，更稳定。

## Gotcha / 边界情况

- **只查第一个实例**：如果一个 Agent 有多个 `SocialNetworkModule` 实例（理论上可能，虽然实践中通常只有一个），这里只用 `instances[0]` 的实例 ID。其他实例的社交实体不会被查询到。
- **`_parse_json` 处理双重编码**：代码里有处理 JSON 双重编码的逻辑（`json.loads` 结果如果还是字符串，再 `json.loads` 一次）。这说明历史数据里存在 `identity_info` 等 JSON 字段被双重序列化的情况，是历史遗留问题。

## 新人易踩的坑

单实体查询接口用 `user_id` 作为路径参数，但实际上查的是 `entity_id` 字段（`WHERE entity_id = {user_id}`）。这个接口的命名继承自最初只处理"用户"类型实体的设计，实际上 `entity_id` 可以是任何类型实体的 ID，不限于用户。
