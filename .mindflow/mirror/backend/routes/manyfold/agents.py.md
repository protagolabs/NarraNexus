---
code_file: backend/routes/manyfold/agents.py
last_verified: 2026-08-17
stub: false
---

## 2026-08-17(补)— import 改走门面;`min_length=1` 是**故意**留着的

两点:

1. 四个符号(`AGENT_TEXT_MAX_LENGTH` / `StrippedText` / `normalize_agent_row_text`
   / `normalize_agent_text`)改从 `xyz_agent_context.schema` 门面引。此前深引
   `entity_schema`,而 mirror md 里给的理由是「成环」—— **假的**:成环只对
   [[api_schema]] 成立(包内,门面反过来导出它的模型);本文件在 `backend/` 下,
   引门面从不成环。当时的真实原因只是 `StrippedText` 没进门面,现在进了。
2. `ManyfoldUpdateAgentRequest.agent_name` 的 `min_length=1` **保留**,与
   [[social_network.py]] 的 `CreateAgentBody` 摘掉它的决定相反 —— 理由不同所以答案
   不同:那边 422 会抢在路由自己的拒绝之前,而那句拒绝的**措辞**是要给 LLM 读的,
   两条孪生路径必须给同一个串;本端点的消费方是 Manyfold 服务,**422 就是契约**。
   而且 `StrippedText` 先归一,`"   "` 到达时已是 `""` 会被拒,不会被存成空白名。
   已在 Field 上就地注明,免得下一个人照 `CreateAgentBody` 的先例把它也摘了 ——
   摘了还必须同时加空名拒绝,否则 `patch` 用 `is not None` 构造,`""` 会进去把名字清空。

测试:`tests/backend/test_agents_row_writers_normalize.py` 的
`TestManyfoldUpsertFallback` 覆盖包裹层**唯一独立负责**的那条支路 —— 调用方省略
字段时回退到库里的老值(模型层的 `StrippedText` 管不到它)。用「摘掉包裹再跑」
验证过会红。

## 2026-08-17 — 三处 agents 直写改为归一后再写

本文件是 `agents` 行的 raw-write 路径之一(不经 [[agent_repository]]),共三处:
`POST /manyfold/agents` 的幂等更新分支与新建分支、`PATCH /manyfold/agents/{id}`。
三处现在都过 [[entity_schema]] 的 `normalize_agent_row_text`。

**为什么这里必须自己做**:改名路径([[auth.py]])比较归一后的值,所以库里若存着
`" name "`,owner 之后把它改成 `"name"` 会被判「已相等」→ 一次写都不发 →
接口答成功、行永远清不掉。PATCH 恰恰**就是一个改名端点**,而从 UI 推过来的名字
带首尾空白是常态输入。

`POST` 那两支的 `or` 兜底同样挪到归一之后:`"   "` 是 truthy,先 `or` 会跳过
兜底把纯空格存成名字(与 [[auth.py]] create 那处同一个陷阱)。

两个请求模型的 `agent_name` / `description` 换成 `StrippedText`,于是 `max_length`
量的是**入库形态**:`"y"*255 + " "` 此前在这里 422、在 `PUT /api/auth/agents` 通过 ——
同一行同一字段同一输入两个答案。四个写边模型现在统一,
`tests/backend/test_agent_request_length.py` 的 strip 边界用例把四个都参数化进去了。

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
