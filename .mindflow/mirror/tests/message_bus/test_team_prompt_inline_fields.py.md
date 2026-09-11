---
code_file: tests/message_bus/test_team_prompt_inline_fields.py
last_verified: 2026-09-11
stub: false
---

## 2026-09-11 — 为什么存在（PR#401 同类扫描）

钉住团队房 prompt 的行语法不可被作者写的标签伪造：成员名/描述里塞换行 + 假成员行 + 假
`· Leader`，名单仍只有两行真成员，每行用严格正则 fullmatch（句柄、JSON 字面量名字、标记、
JSON 字面量描述），标签 `json.loads` 回来等于独立推导的规范化文本，Leader 标记不能伪造；诚实
名单的完整行形态；超长描述截断（现为 80，见下）并以 `…` 标记、超长 id 原样完整；工作板标题伪造的带假
`id=` 行不出现，唯一一行 fullmatch 且 id 为真；巡查停滞列表同理。回退证明：把
[[../../src/narranexus/platform/message_bus/message_bus_trigger]] 回退到改动前 → 5 条全红。

## 2026-09-11 第二轮 — 覆盖到不变量的全部行语法块

每条用例都让伪造者**真的走被测字段**：伪造名字的成员**自己发言**，正文里带假 `User:` 行与假
`"Ana":` 行 → scrollback 只有两条真行头（严格正则 fullmatch，发送者 `json.loads` 回解），假行只以
`BODY_LINE_PREFIX` 续行出现；被点名列表里的伪造名字留在字面量里；批次指向列表同理；诚实消息仍是
`"Ana": hello`。公告栏：agent 规则 `…\n2. …` 不产生第二条无署名编号行，署名在首行正文之前；
`[Team progress]` 摘要整段引用。@mention：每行的 `@token` 经 `extract_team_mentions` 解析恰好回到该
成员、带引号形态解析为空；同前缀/以符号开头的名字显示 `(no @mention token)`。peer 私聊正文伪造不出
`From:` 头；附件文件名带换行不开新行。描述上限改为 80（与 Known Agents 同口径）。回退证明：trigger
回退到本轮前 → 15 条中 13 条红（仅 2 条既有的工作板/巡查用例仍绿，它们上一轮已覆盖）；只回退
`_bus_attachment_impl` → 附件用例红。
