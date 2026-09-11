---
code_file: tests/message_bus/test_unread_preview_truncation.py
last_verified: 2026-09-11
stub: false
---

## 2026-09-11 — 为什么存在

钉住 B-23 / upstream #73：未读列表绝不给 agent 一条被静默截断的消息。用 #73 原文（三段、
约 560 字）断言整条渲染；超出 `UNREAD_PREVIEW_MAX_CHARS` 的行必须带截断声明（原长、展示长、
`read_history`），恰好等于预算的不标；分片行 `(part i/n)` 与截断声明共存；静态块那句
「未读已在 context」必须说明长消息会被截断。把 [[../../plugins/builtin.message_bus/src/narranexus_plugins/message_bus_module/message_bus_module]]
的渲染改回 `[:200]` 硬切，前四条变红。

## 2026-09-11 — review 跟进（PR#401）

新增：#73 原文断言按「首行 + `  > ` 引用续行」的渲染形式整条出现（核心资产保留、改形式）；正文伪造一行他人 row / 伪造表头 / 伪造 cut 标记——列表里只有两条真 row，伪造行全是引用行；cut 标记指路给确切调用（DM→`with_agent`、团队房→`team_id`、无句柄时如实说取不到）；总预算保留最新行、声明被略去的条数并列出对应调用；预算内无省略声明；最新一条单独超预算也照常展示。「无标记」断言改为直接查 `UNREAD_CUT_MARKER` 标记行，不再拿 `read_history` 当代理。回退证明：`_unread_body` 改回原样返回 → 两条结构用例红；总预算置无穷 → 预算用例红；去掉 team_id 分支 → 团队房指路用例红。

## 2026-09-11 — review 二轮跟进（PR#401）

新增：房名含换行+伪造 row 时，未读列表与 `### Your teams` 里 `- \`` 开头的行仍只有真行；Known Agents 的名字/描述同理；窗口外（total=50、窗口 20）按 `total - shown` 声明 30 条未展示；「最新一条单独超预算」用 monkeypatch 把 `UNREAD_SPAN_MAX_CHARS` 压到 200 真正构造该分支（并验证更旧一行被让出）；总预算断言把声明行一并计入；静态块断言两类例外（shown cut / not shown）都在同一句。回退证明：`_bus_tag` 的 label 改回 `.strip()` → 未读房名用例红；去掉「最新一条必留」护栏 → 超预算用例红；整文件回退 → 7 条红。
