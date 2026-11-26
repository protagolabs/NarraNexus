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

## 发往哪里:只认 quota-api 给的 `base_url`

缓存的单位是"key + 发往哪里"(`wallet_client.DecisionKey`)。决策调用的地址**只**来自
quota-api 响应里的 `base_url`(网关本身,如 `http://litellm:4000/v1`,路由是
`{base_url}/alpha/decisions`),**不是** `FREE_TIER_GATEWAY_OPENAI_BASE_URL`:部署里
那个值是沙箱代理 `http://llm-gateway:4000/v1`,它按设计拒绝这个路由(staging 实测 403
"not allowed via llm-gateway",同一把 key 直连网关 200)。早先版本把 key 发往
`free_tier_endpoints().openai_base_url`,云端每次决策调用都会落到失败兜底——这是
staging 联调发现的真 bug。`base_url` 是**必填**:缺失或不是绝对 http(s) URL(更老的
quota-api、格式错)= 密钥服务不可用,与 404 同样处理(进程级暂停、落到下一个凭据源);
不回落到任何配置地址,也没有兼容分支。复查时 key 相同但 `base_url` 变了,同样替换条目。

## 设计决策

- **缓存**：agent_id → 条目（key + 复查时刻），每进程每 agent 一次 quota-api 往返，
  而不是每轮一次。LRU 上限 4096（每条约 100 字节；上限只为长寿进程不无限攒 agent，
  被挤掉的只是多一次取）。首取与复查替换走同一个上限检查——复查等待期间条目可能已被
  挤掉，替换时不能让大小越过上限。
- **401 不让 key 变冷**：网关 401 时（admin key 轮换 / key 被删 / 被运维封禁）
  `on_rejected` 只把该条目标成"已被拒"并记下复查时刻，**照旧发这把 key**——被封禁的
  agent（正是因为刷量被吊销的那个）绝不能借 quota-api 抖动落到不封顶的平台 key 或
  owner key 上。同一轮不重试（延迟预算）。复查最多每个间隔（60s）一次：quota-api
  返回**不同**的 key → 替换（轮换在一个间隔内被拾取）；返回同一把 → 保留并重新信任
  （下一次 401 再标记）；复查失败（不可达 / 拒绝）→ 保留旧 key，再等一个间隔。于是
  被封禁的 agent 每个间隔只花一次 quota-api 调用，而不是每轮一次。只标记**那把** key：
  慢轮次迟到的 401 不会把期间换上的新 key 标成已拒；复查时刻只由**第一次** 401 设定，
  后续 401 不顺延——否则每隔几秒就来一轮的 agent 永远等不到复查。复查也受进程级暂停
  约束（暂停期间不复查，照旧发旧 key）。
  **真实的 admin key 轮换有代价**：活跃 agent 在拾取新 key 之前，最多一个间隔里每轮都
  401、落在失败兜底上（守锚点 / 新建），这是"被封禁 agent 不得改用别的凭据"的另一面。
- **失败分两类**（`_ask`）：
  - **服务级**（传输错误、2s 超时、5xx、408/429（服务忙）、401/403 token 配错、
    表示端点不存在的 404、回复里没有 key 或可用的 `base_url`）→ 进程级暂停 60s，期间**未缓存**的 agent 不问 quota-api，直接用
    平台 key 或 owner key（都没有则两次调用路径）；已持有 key 的 agent 不受影响。
  - **单 agent 级**（408/429 以外的其它 4xx，`WalletRejected`，如 422 agent_id 格式非法）→ 只记在
    该 agent 的条目上（无 key，60s 内不再问），不暂停其它 agent。
  每次失败打一条 warning，不含 key 和响应体。服务级通常每个间隔一条，但失败那一刻
  正在途中的并发请求各打一条；单 agent 级是每个 agent 每个间隔一条。
- **并发不去重**：同一 agent 的并发首轮、以及复查时刻到点时同一 agent 的并发轮次，
  都可能各问一次 quota-api；端点幂等，代价只是重复调用，不会出第二把 key。
- **何时存在**：`decision_key_issuer()` 进程单例（缓存要跨轮），仅当云模式且
  `WalletClient.from_settings()` 非 None（`FREE_TIER_WALLET_API_URL/_TOKEN` 已配）；
  本地/桌面恒为 None，行为与之前一字不差。没有新增环境变量。测试里
  `tests/conftest.py` 的 autouse fixture 每个测试前后清掉该缓存并删掉两个钱包变量。
- **冷缓存的代价落在每轮解析上**：凭据在合并路径之前解析，所以快门本可零调用回答的
  轮次在冷缓存时也付一次取 key——每进程每 agent 一次（或每个失败间隔一次），上限
  2s；热缓存只是一次字典查找。

## 上下游关系

- **被谁用**：`narrative/_narrative_impl/merged_router.decision_endpoint_for_turn`。
- **依赖谁**：[[wallet_client]]（`decision_key` / `DecisionKey`）、
  `decision_model.AgentScopedKey`。
- **测试**：`tests/integrations/free_tier/test_decision_keys.py`（缓存/401 后复查/两类失败/
  超时/上限/工厂）、`tests/narrative/test_merged_routing.py`（端到端）。
