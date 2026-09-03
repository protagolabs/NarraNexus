---
code_file: src/narranexus/contracts/agent_events.py
last_verified: 2026-09-03
stub: false
---

## 2026-09-03 — agent-loop 事件字典契约的正式家

从 `agent_framework/loop/events.py` 逐字迁来（两个事件族、data/item 类型常量、CLI 错误词表、usage
键名、四个 TypedDict、两个构造器）。这是每个 `AgentLoopDriver` 产出、`ResponseProcessor` 消费的
隐式契约的显式化（研究文档「event dict 无 schema 文档」一项）。线上值经 executor NDJSON 原样传输，
属于跨版本协议：变更必须 bump `API_VERSIONS["agent_events"]`，`tests/snapshots/golden/agent_events.json`
让任何漂移在 CI 变红。stdlib-only，保持契约包为叶子。
