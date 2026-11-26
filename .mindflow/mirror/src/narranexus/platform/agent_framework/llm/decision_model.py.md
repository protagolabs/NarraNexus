---
code_file: src/narranexus/platform/agent_framework/llm/decision_model.py
last_verified: 2026-11-25
stub: false
---

# decision_model.py — 决策模型客户端(厂商中立)+ 每轮凭据解析

## 为什么存在

决策模型(当前是 TypeSafe `jev-1.13`,经 `/alpha/decisions` 提供)和 helper
LLM 是两种调用形状:它不写文本,只对一个 state 回答若干 typed 问题,每题回一个
"是"的概率。硬塞进 `helper_sdk.llm_function`(instructions + user_input →
final_output)只会扭曲那个契约,所以单独一个小 API。第一个消费者是叙事合并路由
(`narrative/_narrative_impl/merged_router.py`)。

## 两个单元,各自可单测

1. **客户端** `ask_yes_no(endpoint, state, questions)`:一次请求、无重试、硬总
   超时(`asyncio.timeout` 包住整次调用,不是 httpx 的分阶段超时)。公开面
   (`YesNoQuestion` / `DecisionResult` / `DecisionEndpoint` / `DecisionModelError`)
   不含任何厂商名;换厂商 = 换 `ask_yes_no` 的函数体,调用方不动(铁律 #9)。
   本模块不读环境变量,base/key/model/timeout 由调用方的配置以
   `DecisionEndpoint` 显式传入。
2. **凭据解析** `resolve_decision_endpoint(...)`:每轮调用一次,顺序固定:
   - 平台 key(调用方传入,叙事侧即 `NARRATIVE_DECISION_API_KEY`)非空 → 用它;
   - 否则当前用户自己的 provider 凭据,**只当其 base URL 的 host 等于决策 base
     的 host** 时才借用其 key;请求仍发往决策 base(只借 key,不借路径)。
     检查顺序:helper 槽 openai 协议(`openai_config`)→ helper 槽 anthropic 协议
     (`anthropic_helper_config`)→ agent 槽 claude(`claude_config`)→ agent 槽
     codex(`codex_config`),取第一个合格者;OAuth 类 auth_type 跳过
     (token 绑定签发方,别处无效);cli_helper(订阅/OAuth)不参与;
   - 都没有 → None。

## 设计决策

- **host 相等是全部规则**:云端免费档用户的 provider 指向内部网关
  (`llm-gateway` / `litellm`,虚拟 key),其它 OpenAI 兼容聚合商同理——host
  不同就不合格,不靠任何厂商名判断。同一厂商的另一协议端点(同 host)合格,
  因为是同一把 key。
- **读的是 helper SDK 读的那一层**:`api_config` 的模块级 proxy(当前 task 的
  ContextVar,缺省回落全局 holder——本地/桌面模式的 llm_config.json / .env)。
  不自己查库、不重做 provider 解析;并发轮次各自的 ContextVar 互不可见,解析出
  的 key 以 `DecisionEndpoint` 显式传给客户端,没有进程级全局 key。
- **失败永远不是分数**:缺 key、非数字、bool、NaN、越界、非 200、非 JSON、
  传输错误、超时一律 `DecisionModelError`;客户端不拿 0.0/0.5 补洞——猜出来的
  分数在下游与真答案无法区分。
- **key 不外泄**:只进 Authorization 头;错误文案在本模块自建并擦除 key
  (防服务端回显),原异常不链接(`from None`),`DecisionEndpoint` 的 repr 不含 key。
- **每次调用一个短命 httpx 客户端**(仓内出站调用的主流写法);可注入 `client`
  供测试。代价是每轮一次 TLS 握手,换来零生命周期管理与跨事件循环安全。
- `serialise` / `encode_questions` 公开,是为了调用方统计"发了多少字符"时与线上
  编码逐字一致(叙事审计 `merged_input_chars`)。

## 坑

- 测试里往 `api_config` 的 proxy 对象上 `monkeypatch.setattr` 字段,undo 会把旧值
  钉成实例属性,之后该 proxy 不再读 ContextVar,依赖用户凭据解析的测试就会因
  执行顺序而失败。`tests/conftest.py` 的 autouse 拆钉 fixture 负责清理;新测试要
  换用户配置请用 `set_user_config`(在独立 task 里),不要直接改 proxy。
