---
code_file: src/narranexus/platform/agent_framework/llm/decision_model.py
last_verified: 2026-11-26
stub: false
---

# decision_model.py — 决策模型客户端(厂商中立)+ 每轮凭据解析

## 为什么存在

决策模型(当前默认是 TypeSafe `jev-1.14`,经 `/alpha/decisions` 提供)和 helper
LLM 是两种调用形状:它不写文本,只对一个 state 回答若干 typed 问题,每题回一个
"是"的概率。硬塞进 `helper_sdk.llm_function` 只会扭曲那个契约,所以单独一个小
API。第一个消费者是叙事合并路由(`narrative/_narrative_impl/merged_router.py`)。

## 两个单元,各自可单测

1. **客户端** `ask_yes_no(endpoint, state, questions)`:一次请求、无重试、硬总
   超时(`asyncio.timeout` 包住整次调用)。公开面不含任何厂商名;换厂商 = 换
   `ask_yes_no` 的函数体(铁律 #9)。本模块不读环境变量,base/key/model/timeout
   由调用方以 `DecisionEndpoint` 显式传入。
2. **凭据解析** `resolve_decision_endpoint(...)`:每轮调用一次,**凭据策略只住在
   这一个函数里**(平台 key 适用于哪些用户等策略将来只改这里):
   - 调用方传入的 `agent_scoped`(`AgentScopedKey`:为本轮 agent 单独签发的 key +
     它必须发往的 base)可发送 → 用它,请求发往**它自己的 base**,
     `key_source=agent_scoped`(用量是平台的花费,不记 owner 的账);取这把 key 要
     agent id、冷缓存时要走网络,所以由调用方异步取好再传进来,本函数保持同步、
     不认识任何厂商/网关;
   - 否则平台 key 可发送(见下)→ 用它,`key_source=platform`,对所有轮次生效;
   - 否则 **agent owner 的 active helper 槽凭据**——即该轮 ingress
     (`get_agent_owner_runtime_llm_configs`)装进当前 task 的配置——按
     `get_helper_sdk` 的同一优先级取:装了订阅/CLI helper → 无 key 可借;装了
     anthropic 协议 helper → 只看它;否则看 openai 协议配置。要求 api-key 类 auth、
     key 可发送、且其 base URL 的 host 等于决策 base 的 host;请求仍发往决策 base
     (只借 key),`key_source=helper_llm`;
   - 都没有 → None(该轮走两次调用路径)。

## 设计决策

- **签发方的 401 钩子挂在 endpoint 上**:`DecisionEndpoint.on_rejected`(不参与
  repr/比较)。`ask_yes_no` 收到 **401** 时先调它再抛 `http_401`——签发方借此安排
  复查(轮换 / 吊销;如何处理是签发方的事,见 `integrations/free_tier/decision_keys`);其它任何失败(403/422/5xx/超时)都不调。客户端
  只知道"凭据被拒",不知道是谁签发的,换签发方不动调用方。

- **只认当前 task 上真正装好的配置**(`snapshot_user_config()`),不读 proxy 的
  进程级 holder 回落:每一轮的 ingress 在云端与本地模式下都会装 owner 的已解析
  配置(本地模式只是 `ProviderResolver.resolve` 返回 None、改走严格 own-config
  解析,**仍然装到 task 上**),所以没装配置的 task 不是一轮对话,而 holder 回落
  会把部署方自己的凭据交给它。
- **只借 helper 槽,不借 agent 槽**:借来的 key 产生的费用要记到出钱的那张卡上;
  `cost_tracker.record_cost` 的卡归属由 `get_provider_card_source(call_type)` 二选一
  ——只有 `agent_loop` 归 agent 卡,其它一律归 helper 卡。agent 槽 key 的决策调用
  要么被记成 `agent_loop`(在费用页上冒充主模型),要么记到 helper 卡(错卡)。
  两者都是失真,所以 agent 槽凭据不借。取 *active* helper(而不是"装了的任意一个")
  也是为了让被借的 key 恰是 helper 卡的 key。
- **host 相等是全部规则**:免费档 owner 的 provider 指向内部网关(`llm-gateway` /
  `litellm`,虚拟 key),其它 OpenAI 兼容聚合商同理——host 不同就不合格,不靠厂商
  名判断;因此免费档网关卡**永远不会被借**,也就不会有决策调用打到它的配额上。
  同一厂商另一协议端点(同 host)合格,是同一把 key。
- **key 必须可发送**:非空、可打印 ASCII、无空白/控制字符。含 `\n`/`\r\n`/`\x00`
  的 key 会让 h11 抛 `Illegal header value b'Bearer sk-...'`,非 ASCII 会抛
  `UnicodeEncodeError`——两者的文本都会把 key(repr 转义后,擦除不到)带出来。所以
  这样的 key 一律**不算凭据**(平台 key 不可发送或首尾带引号时叙事启动校验直接拒绝启动),不做
  strip-and-use。
- **失败永远不是分数**:缺 key、非数字、bool、NaN、越界、非 200、非 JSON、传输/编码
  错误、超时一律 `DecisionModelError`。`category` 是固定词表(`unconfigured` /
  `invalid_request` / `timeout` / `transport` / `http_<status>` / `malformed`),
  是**唯一**允许进用户可见面的部分;`str(e)` 只进日志:传输类只含异常类型名,
  服务端文本擦除 key,原异常不链接(`from None`)。
- **每次调用一个短命 httpx 客户端**(仓内出站调用的主流写法);可注入 `client`
  供测试。代价是每轮一次 TLS 握手。
- `serialise` / `encode_questions` 公开,是为了调用方统计"发了多少字符"时与线上
  编码逐字一致(叙事审计 `merged_input_chars`)。

## 坑

- 测试里装用户配置请用 `set_user_config`(在独立 task 里),`cli_helper` 传 None
  (生产 ingress 只在订阅 helper 时才装它,装了就没有 key 可借)。
- 往 `api_config` 的 proxy 对象上 setattr 会钉住字段;`tests/conftest.py` 每个测试
  后调 `api_config.unpin_config_proxies()` 清理。本模块已不读 proxy,但其它测试仍受益。
