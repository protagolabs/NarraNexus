---
code_file: src/narranexus/platform/integrations/free_tier/wallet_client.py
last_verified: 2026-11-26
stub: false
---

# wallet_client.py — NarraNexus 看待「钱包服务」的全部视角

## 为什么存在

免费额度的钱在 LiteLLM 网关上，而网关的 admin key **只有 deploy 仓的
quota-api 持有**（这正是那个服务存在的理由：后端不该拿到能给任何人开钱包的
凭据）。本文件是这条边界的唯一穿越点：开钱包、查余额、取 agent 的决策路由 key，仅此而已。我们从不
知道「虚拟 key」是什么概念 —— 拿到的就是一个不透明的 api_key，和用户自己
粘贴一把 NetMind key 没有区别。

## 为什么放在 agent 包而不是 backend

**两边都要用**：登录路径要开钱包（backend），而 agent 侧的转录 resolver 要
问「这个用户还有没有免费余额」。按铁律 #21 的单向依赖，backend 可以 import
agent 包、反过来不行，所以共用的那块必须落在这里。

## 错误分成四类，是有代价才分的

调用方对三者的反应完全不同，合并成一个异常就等于把「部署配错了」伪装成
「这个用户没有免费额度」：

- `WalletUnavailable` —— 传输层 / 5xx / 408 / 429。瞬时，登录路径吞掉，下次登录重试。
  408/429 说的是服务忙、不是输入错，所以归这里而不是 `WalletRejected`——限流时调用方
  暂停一次，而不是逐个 agent 重试。
- `WalletDenied` —— 401/403。共享 token 配错了，重试没用，必须在日志里刺眼。
- `WalletMissing` —— 余额 404。用户就是还没开钱包（新用户会有几秒处于此态）。
- `WalletRejected` —— 其它 4xx（408/429 除外）。服务正常，但拒绝了**这次请求的输入**（如 422），
  原样重试无用。此前它混在 `WalletUnavailable` 里，与模块头"Unavailable = 传输 /
  5xx"的定义不符；拆开后决策 key 的调用方才能区分"服务挂了（暂停所有 agent）"与
  "这个 agent 被拒（只记在它身上）"。其它调用方都按 `WalletError` 捕获，行为不变。

## decision_key(agent_id)

`POST /v1/decision-keys` 取该 agent 在网关决策路由上的封顶 key **和发往的地址**，
返回 `DecisionKey(api_key, base_url)`（deploy 侧 quota-api 签发，幂等：同一 agent
每次同一把，admin key 轮换后才会变）。`base_url` 是网关本身，调用方必须用它而不是
自己发 LLM 请求的地址（部署里那是沙箱代理，按设计拒绝决策路由）。调用方
[[decision_keys]] 按进程缓存。错误沿用分类词表，唯一例外：这里的 **404 不是
「没有钱包」**，而是部署的 quota-api 早于这个端点，所以转成 `WalletUnavailable`
（消息点名端点）；回复里没有非空字符串 `api_key`、或没有绝对 http(s) `base_url`
（早于该字段的 quota-api / 格式错）同样是 `WalletUnavailable`，不猜地址。key 不进
任何日志、异常文本或 `repr`。

## served_models() 为什么问网关而不是上游

网关只路由（也只知道价格）它被配置的那些模型。上游 NetMind 卖的目录大得多 ——
把上游目录塞进免费卡的下拉框，用户选中一个网关没配的模型，第一次调用就 400；
就算没 400，那个模型也算不出钱。dev 上实测：上游 43 个 vs 网关 15 个。

## 注意

`from_settings()` 未配置时返回 `None` 而不是抛异常 —— 让「这个部署里根本没有
免费额度」在每个调用方都收敛成一句 `is None`。
