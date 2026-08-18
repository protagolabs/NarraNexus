---
code_file: backend/routes/manyfold/agents.py
last_verified: 2026-08-17
stub: false
---

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
