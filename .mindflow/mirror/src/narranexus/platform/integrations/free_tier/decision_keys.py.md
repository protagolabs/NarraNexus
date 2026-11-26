---
code_file: src/narranexus/platform/integrations/free_tier/decision_keys.py
last_verified: 2026-11-26
stub: false
---

# decision_keys.py — 免费档网关为每个 agent 签发的决策模型 key

## 为什么存在

Owner 决议：云端叙事路由问决策模型时一律走免费档 LiteLLM 网关，**每个 agent 一把
封顶 key**（deploy 侧默认 $1、按月清零）。上游 key 只留在网关里，一个被刷爆的
agent 也只能烧掉自己那 $1。key 由 deploy 侧 quota-api 幂等签发；本文件在进程内
缓存它，并以通用的 `AgentScopedKey` 交给 `decision_model.resolve_decision_endpoint`
——优先级本身不住这里。

## 设计决策

- **缓存**：agent_id → key，每进程每 agent 一次 quota-api 往返，而不是每轮一次。
  LRU 上限 4096（每条约 80 字节；上限只为长寿进程不无限攒 agent，被挤掉的只是多
  一次取）。
- **401 回收**：返回的 `AgentScopedKey.on_rejected` 绑定 `forget(agent_id, key)`；
  网关 401（admin key 轮换 / key 被删）时由决策客户端调用，下一轮重取。同一轮不
  重试（延迟预算）。只删**那把** key：慢轮次迟到的回收不会把期间新取的 key 删掉。
  被封禁（block）的 key quota-api 原样返回，于是该 agent 每轮多一次 quota-api 往返
  并失败——这是封禁的预期形态，不另做处理。
- **失败不拖住本轮**：取 key 有 2s 总墙（只在冷缓存时付，叠加在决策调用自己的超时
  之前）；任何 `WalletError`（不可达 / 5xx / 401 token 配错 / 404 旧版 quota-api /
  422）或超时 → 返回 None，解析器落到下一个凭据源。
- **负缓存**：一次失败后 60s 内不再问 quota-api（进程级，不分 agent）——宕机或旧版
  quota-api 不能让每轮都付超时。已缓存的 key 不受影响。422（agent_id 格式非法）理论
  上不会发生（app 签发的 id 恒为 `agent_<hex>`），若发生也只是让未缓存的 agent 晚
  60s 拿到 key。
- **并发首轮不去重**：同一 agent 的并发首轮可能各取一次；端点幂等，代价只是重复
  调用，不会出第二把 key。
- **何时存在**：`decision_key_issuer()` 进程单例（缓存要跨轮），仅当云模式且
  `WalletClient.from_settings()` 非 None（`FREE_TIER_WALLET_API_URL/_TOKEN` 已配）；
  base 取 `free_tier_endpoints().openai_base_url`（与免费档 provider 卡同一个值）。
  本地/桌面恒为 None，行为与之前一字不差。没有新增环境变量。

## 上下游关系

- **被谁用**：`narrative/_narrative_impl/merged_router.decision_endpoint_for_turn`。
- **依赖谁**：[[wallet_client]]（`decision_key`）、`providers/free_tier`（网关
  base）、`decision_model.AgentScopedKey`。
- **测试**：`tests/integrations/free_tier/test_decision_keys.py`（缓存/回收/负缓存/
  超时/上限/工厂）、`tests/narrative/test_merged_routing.py`（端到端）。
