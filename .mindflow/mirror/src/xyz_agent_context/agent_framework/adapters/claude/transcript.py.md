---
code_file: src/xyz_agent_context/agent_framework/adapters/claude/transcript.py
last_verified: 2026-07-29
stub: false
---

# transcript.py — 自己写 `--resume` 要读的那个文件

## 2026-07-29 — 落盘与删除(T2)

新增 `write_transcript` / `remove_transcript`,把"构建 → 使用 → 删除"这个生命
周期做成两个函数。

**`write_transcript` 返回 `None` 而不抛异常。** `None` 的含义是"这一轮走老路":
没有可续的历史,或者文件写不进去。两者都不是事故——transcript 是优化,调用方在
它缺席时把历史留在提示词里。在这里抛异常会把"磁盘满"变成"agent 运行失败",
铁律 #14 禁止。

**捕获范围刻意比 `OSError` 宽。** 明显的失败是 IO(磁盘满、目录只读、路径被目录
占用),但一个不可序列化的值进到 `render` 会抛 `TypeError`、从更窄的 except 里
逃出去。fail-open 若只覆盖一部分失败,就是句空话。同理 `build_records` 里把
`working_path` 强制转 `str`——注解写着 `str` 但没人强制,`Path` 对象只会在更晚的
json 序列化处炸。

**`remove_transcript` 永不抛。** 它从 `finally` 里被调用,在那里抛异常会**掩盖
真正结束这个 turn 的错误**。还必须容忍它可能遇到的每种状态:`None`(写失败或
跳过)、已经不存在、路径不是文件。

删除不是打扫卫生:留在共用 `CLAUDE_CONFIG_DIR` 里的 transcript 正是
`executor_resume_hmac_secret` 要防的跨租户读取路径(`/agent-loop` **刻意无鉴权**,
句柄可猜就能读别人对话),而且会无界增长。磁盘上没有留存物,就没有可读的东西。

## 为什么存在

缓存前缀是严格字节匹配,顺序 `tools → system → messages`。历史塞进 system
prompt 就等于塞进**前缀内部**:每轮新增一句都改动那段字节,把它后面的一切作废。

agent-loop resume 是第一版解法——给 CLI 一个会话句柄,历史走 CLI 自己的
transcript。它确实生效了(线上实测:第二个连续 resume 轮全价 input 从 49,137
降到 2,247,−95.4%)。

但它留了一笔成本。**冷轮**仍把历史放在 system prompt 里(实测 63,603–66,023
字符),**resume 轮**不放(63,244),两个提示词因此不同,于是**任何冷轮之后的第
一个 resume 轮必然从 `system` 开始 miss** —— 每次冷启动约 49K 全价。而冷启动
的触发原因完全不在缓存的控制内:还没有句柄、叙事变了、句柄过期。

自己写 transcript 把冷轮/resume 轮的区别整个消掉:每一轮都是 resume 轮,system
prompt 从第一轮起就逐字节相同,历史落在它该在的位置——前缀之后的尾部。

## 落地前实测过什么

对 CLI 2.1.220,经抓包代理(脚本在 `reference/self_notebook/experiments/`):

- **E4** —— CLI 接受我们写的 transcript,用它从未签发的 session id 也行,注入
  的回合确实进入请求的 `messages`
- **E5** —— `tool_use` / `tool_result` 配对同样能往返,所以将来给历史补上工具
  记录是可行的(本文件**故意不做**,见下)
- **E6** —— 构建 → 使用 → 删除 → 重建,请求字节不变,这才让"用完即删"的
  生命周期是安全的
- **T0** —— 每轮换 session_id,`tools` / `system` / `messages` 逐字节不变。
  信封字段是 CLI 的簿记、不进请求,**这正是"每轮新 id"安全的原因**,而每轮新
  id 又是"用完即删"成立的前提

## 三个承重细节(都是观察得来,非文档)

1. **文件是树,不是日志。** CLI 从末尾 `last-prompt` 记录的 `leafUuid` 出发,
   顺着 `parentUuid` 往上回溯。顺序对但链断了、或没有叶子指针,文件能解析、
   resume 什么也续不上。
2. **确定性是缓存要求,不是整洁癖。** `messages` 也进缓存,所以同一批回合重建
   必须逐字节一致。每个 uuid 用 `uuid5` 派生、每个时间戳由序号算出——这里放一个
   `now()`,单轮跑起来完全正常,然后永久静默地付全价。
3. **`version` 把这个文件耦合到 CLI 版本。** pin 在 [[cli_binary]] 的
   `PINNED_CLI_VERSION`;bump 它就必须重跑 E4/E5/E6,因为这是个会在我们脚下
   变化的内部契约。

## 为什么 `message.id` 只从位置派生

早期版本从 `session_id` 派生它,于是换 session id 时 `message` 载荷也跟着变。
T0 证明 CLI 重建请求时会丢掉这个 id、所以不影响缓存——但**依赖"CLI 会丢掉它"
不如让它本来就不变**:这样"同一段对话的 message 载荷恒定"这条不变量由我们保证,
而不是靠 CLI 保持某个行为。

## 为什么故意只放纯文本

历史内容与今天 system prompt 里的完全一致——`ContextRuntime.extract_narrative_data`
从每个 event 的 `env_context["input"]` 和 `final_output` 生成的纯文本
user/assistant 对。**只换通道、不换内容**,token 变化才可归因;同时加工具记录
会把两个效应搅在一起。

E5 已证明工具那条路可行,而 `events.event_log` 也已经存着所需字段
(`tool_call` 带 id 与 arguments,`tool_output` 现在也带上了配对 id ——
见 [[execution_state]] 同日条目)。补工具是一次**独立可度量**的改动。

## 输入来自哪里

`build_records` 吃的 `history_entries` 正是 [[materializer]] 的
`split_for_argv` 已经产出的那个列表(`{"role","content"}`,oldest 优先)。刻意
复用平台既有的历史来源,不引入第二个会与它漂移的源。

空历史返回 `[]` —— 没有可续的东西,调用方必须走真正的首轮,而不是写一个 CLI
会拒绝的空文件。
