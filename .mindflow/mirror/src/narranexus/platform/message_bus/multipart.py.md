---
code_file: src/narranexus/platform/message_bus/multipart.py
last_verified: 2026-09-09
stub: false
---

# multipart.py — 长消息分片发送与收件方重组

## 为什么存在

bus 本身不截断（`bus_messages.content` 是 TEXT），截断发生在**发件方模型**：约 4-5k 字的
回复正好撞到工具调用参数 JSON 的输出 token 预算，loop 唯一诚实的回答是「工具没执行，
分小块发」（NexusPower `unparsed_call_result`）。没有分块契约时，「小块」= N 条独立 bus
消息，而 wake 信号在第 1 块落地时就启动收件方的 turn——收件方回答一个片段，后续块又
变成对那个回答的回复。8/31 用户报的三件事（长回复被截、收件方 turn 空、"This turn
ended without delivering a reply" 反复）就是这个形状。

## 契约

`message_agent(text, part_index=i, part_count=n)`。每块各自一行 `bus_messages`（不截、
不重编码），`part_group` = 第 1 块的 message_id，由写入边 `LocalMessageBus._resolve_part_group`
解析：块 >1 必须紧跟同 sender 同 channel 的**最近一块**（index-1、同 count），否则拒绝——
放不进组的碎片不允许存在。块数没有上限（review I7 起以整组字节预算为唯一主约束，见 [[local_bus]]）。
车道 batch 是整条 lane 的 LIMIT 而不是这个组的，所以组可能被批次边缘切断（review I2）：
调用方传 `batch_truncated=True` 时不完整的组**一律 hold**、不做过期/取代判定（部分证据下的
判定会把还躺在表里的块标成「never arrived」）；组之前的行照常投递并 ack，下一批就更靠近组；
组顶在批次边缘、什么都推不动时，trigger 用 `PENDING_BATCH_LIMIT_WIDE` 再读一次。

收件侧 `assemble(batch)` 返回 `(deliverable, held)`，**唯一不变式**（review I3/I6）：车道的 ack
游标只推进到「实际投递的最新一行」，任何被 hold 的行都不能在游标之下。所以 `deliverable`
按 `created_at` 排序（合成消息坐在末块的时间上，`relevant[-1]` 既是触发消息也是 ack 高水位），
且**最早被 hold 的行之后的一切都跟着等**——组之后到的无关消息若现在投递，游标就越过了
part 1，组丢失。组之前的照常投递（不再整批陪跑 600s）。三种组结局：
- **不完整且年轻**（距最新一块 < `PART_ASSEMBLY_GRACE_SECONDS`=600s）→ 该组及其后的行被扣住
  （`held=True`），之前的行照常投递并 ack 到它们的最新一行；最后一块自己的 `wake_signal.bump`
  会把轮询拉回来。600s 刻意宽：
  发件方是 turn 中的模型，思考型模型两次工具调用之间可以几分钟；这个数只约束「发件方
  在两块之间死掉」能让收件方等多久。
- **完整** → 合成一条：content 用空串拼（它们是子串不是段落）、attachments 取并集、
  身份（message_id/event_id/root_run_id/turn source）取第 1 块、`created_at` 取最后一块
  （ack 游标必须越过每一行）、`part_message_ids` 列出全部行（投递回执要逐行盖）。
- **不完整但过期或被取代**（同 sender 在本 channel 又开了更新的组——写入边只会延续
  最新一块，旧组永远续不上）→ 按到达的内容投递 + 明确标记缺哪几块。宁可标记也不静默
  丢（铁律 #16），不永远等（铁律 #14：平台不做打断源）。

## 整组预算（review I7）

`MAX_MULTIPART_TOTAL_BYTES = 200_000` 是多段消息的**唯一**主约束（块数不设上限）：写入边
[[local_bus]] `_resolve_part_group` 把组内已存块的字节数（Python 里按 UTF-8 算，不用 SQL
`LENGTH()`——SQLite 数字符、MySQL 数字节）加上本块，超了就以 `group_budget_reason` 拒绝本块，
已存的块原样保留，绝不裁剪。单行 60 KB 是列的物理上限，与之是两个不同的拒绝理由（「这一块
放不进一行」vs「整条消息太长，拆成两条」）。

## 常量归属（review I4）

`MAX_BUS_MESSAGE_BYTES` 与 `oversize_reason()` 住在本文件（单一家），由 [[local_bus]]
`send_message` 对所有写入方执行；`message_agent` 工具不再自带副本。

## 边界

`MessageBusModule.gather` 的未读预览仍逐行显示（带 `(part i/n)` 标签），只有 trigger 的
turn 入口做重组；Agent 收件箱线程也按块记录（内容完整，只是分行）。
`test_multipart_messages.py` 钉 12k 字节级往返、hold、取代、过期标记、乱序/超长拒绝；
`test_multipart_mysql.py` 钉那条组查找 raw SQL 的 MySQL 方言。
