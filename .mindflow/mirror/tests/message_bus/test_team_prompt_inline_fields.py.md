---
code_file: tests/message_bus/test_team_prompt_inline_fields.py
last_verified: 2026-09-11
stub: false
---

## 2026-09-11 — 为什么存在（PR#401 同类扫描）

钉住团队房 prompt 的行语法不可被作者写的标签伪造：成员名/描述里塞换行 + 假成员行 + 假
`· Leader`，名单仍只有两行真成员，每行用严格正则 fullmatch（句柄、JSON 字面量名字、标记、
JSON 字面量描述），标签 `json.loads` 回来等于独立推导的规范化文本，Leader 标记不能伪造；诚实
名单的完整行形态；超长描述截到 120 并以 `…` 标记、超长 id 原样完整；工作板标题伪造的带假
`id=` 行不出现，唯一一行 fullmatch 且 id 为真；巡查停滞列表同理。回退证明：把
[[../../src/narranexus/platform/message_bus/message_bus_trigger]] 回退到改动前 → 5 条全红。
