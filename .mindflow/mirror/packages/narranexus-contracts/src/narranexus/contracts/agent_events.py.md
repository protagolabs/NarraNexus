---
code_file: packages/narranexus-contracts/src/narranexus/contracts/agent_events.py
last_verified: 2026-09-09
stub: false
---

## 2026-09-09 — `cli_error_self_serviceable` + `response.error.self_serviceable?`

新增纯函数 `cli_error_self_serviceable(error_type) -> bool`：用户能否**自己**清掉这个错
（等/升级 rate limit、重新登录、充值）——`rate_limit` / `authentication_failed` /
`billing_error` → True，`server_error` / `invalid_request` / `unknown` 及平台侧扩展（如
`no_output`）→ False；每个 `CLI_ERROR_TYPES` 值都显式落位（测试钉住）。`response.error` 的
data 多一个**可选**键 `self_serviceable`（`RawResponseData` 同步），只有会分类的 driver
（claude）写它。加键不改任何线上值，golden 快照不动、`API_VERSIONS["agent_events"]` 不 bump。
放在契约包（叶子、stdlib-only）是因为 driver 与 `response_processor` 两侧都要同一份口径。

## 2026-09-03 — agent-loop 事件字典契约的正式家

从原 `agent_framework/loop/events.py` 逐字迁来（该文件已删除，17 个导入方全部改指这里，铁律 #2 不留垫片）（两个事件族、data/item 类型常量、CLI 错误词表、usage
键名、四个 TypedDict、两个构造器）。这是每个 `AgentLoopDriver` 产出、`ResponseProcessor` 消费的
隐式契约的显式化（研究文档「event dict 无 schema 文档」一项）。线上值经 executor NDJSON 原样传输，
属于跨版本协议：变更必须 bump `API_VERSIONS["agent_events"]`，`tests/snapshots/golden/agent_events.json`
让任何漂移在 CI 变红。stdlib-only，保持契约包为叶子。
