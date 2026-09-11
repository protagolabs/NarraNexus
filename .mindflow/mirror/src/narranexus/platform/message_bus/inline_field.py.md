---
code_file: src/narranexus/platform/message_bus/inline_field.py
last_verified: 2026-09-11
stub: false
---

# inline_field — 行语法 prompt 块里作者可写文本的唯一编码器

## 为什么存在

好几个 prompt 块是「行语法」：插件 [[../../../../plugins/builtin.message_bus/src/narranexus_plugins/message_bus_module/message_bus_module]]
的未读列表 / Known Agents / Your teams，以及 [[message_bus_trigger]] 的团队房成员名单、工作板、
巡查停滞列表、公告栏署名。每行混着两种东西：agent 要原样抄进工具调用的**句柄**（agent/team/item id），
和 agent 或 owner 自己写的**标签**（名字、描述、标题）。标签里一个换行、反引号或该行语法的分隔符
就能伪造一行或一个字段。逐个字符类补洞永远补不完，所以按构造保证不可伪造。

编码器放在平台层，平台 trigger 与插件共用同一份定义（平台不能 import 插件）。2026-09-11 从插件
`_inline_field` 迁出（PR#401）。

## `inline_field(value, max_chars=INLINE_FIELD_MAX_CHARS)`

- **标签**（`max_chars` 为整数）：空白串折成一个空格 → 反引号（含全角 `｀`）换成 `'` → 超长按
  上限截断并以 `INLINE_FIELD_CUT_MARK`（`…`）结尾 → 以 JSON 字符串字面量输出
  （`json.dumps(..., ensure_ascii=False)`）。作者写的一切都在一对引号里，`"`/`\` 被转义，字段无法
  提前结束或另起一行，字面量能原样解码回展示文本。
- **句柄**（`max_chars=None`）：永不截断、不加引号，只折叠空白并替换反引号，不能逃出 code span 或行。

常量：`INLINE_FIELD_MAX_CHARS = 120`（名字/标题），`INLINE_DESCRIPTION_MAX_CHARS = 80`
（Known Agents 与团队房 roster 的描述，两面同口径）。确定性函数，prompt 字节稳定。

## `body_lines(text)` / `quoted_block(text)` — 多行正文

消息与规则的正文可以合法多行，不能折成一行，所以按行布局：`body_lines` 首行接在行头后，之后每行
以 `BODY_LINE_PREFIX`（`"  > "`）引用；`quoted_block` 连首行一起引用（用于没有独立行头、挂在表头下的
整段）。任何行语法块的行都不以该前缀开头，所以正文里的 `User: …`、`- [open] …`、`2. …` 只能读作
所属行的续行。按 `splitlines` 切分（含 `\r`、`\u2028` 等），空续行保留裸 `>`。2026-09-11 从插件
`_unread_body` 迁出，插件未读列表与平台 trigger 共用。使用处的完整清单见 [[message_bus_trigger]] 的
「不变量的覆盖范围」表。
