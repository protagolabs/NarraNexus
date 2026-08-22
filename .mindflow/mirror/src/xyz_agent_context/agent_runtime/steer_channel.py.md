---
code_file: src/xyz_agent_context/agent_runtime/steer_channel.py
last_verified: 2026-08-22
stub: false
---

# steer_channel.py — orchestrator 往运行中 run 推消息的句柄

orchestrator 起可 steer 的 run 时把它登进 RunRegistry 并留着;有新消息就 `push`。channel 把注入渲染成
provider message 塞进一个普通 asyncio.Queue。

**一条 channel 两种投递**:
- **进程内**(loop 在本进程):loop 的 `QueueSteeringInlet` 直接建在**这条 channel 的 queue** 上——push
  即到 drain 处,无 pump 无拷贝。
- **子进程/远程**:driver 起个小 pump 抽干 channel、把每条写下 runner 的 steer 传输(本地 stdin 行 / 云端
  executor steer 端点),runner 喂自己的 inlet。pump 与 driver 同在,跟 transport 一起加。

`render_injection` 是 `SteerInjection`→provider message 的**唯一**转换点:英文 tag 标来源(队友房间消息 vs
主人插话,措辞不同、机制同),用户原文不动;注入仍是 `user` 消息(纯追加、护缓存)。

## 坑

channel 的 queue **无界**是有意的:有界、back-pressure 的写边界是 `steer_inbox`(producer 写那里被挡);
这条 queue 是"已准入的在飞交接",不再设限。`push` 用 `put_nowait` 在 run 自己的事件循环上(线程亲和,
见 [[steering.py]] 写入端契约)。

## 2026-08-22(补)— review 收敛:nonce 化防伪造 + 告警可定位/沿边触发

**防伪造(真做,非文案)**:`render_injection` 把来源 tag 和正文**结构分离**,且分离**不可伪造**:平台 tag
是前导行,正文包在 `<message {nonce}>…</message {nonce}>` 块里,`nonce` 每次渲染**新随机**(`secrets.token_hex(4)`,
项目 ID 惯例)。owner/teammate 是真权限区分(agent 手握 shell/文件/MCP),所以队友绝不能让模型把自己的话读成
主人的。发送方**无法预知 nonce**→写不出匹配的 `</message {nonce}>` 提前闭合→正文里任何 `</message>` 都被困在
块内当字面量。prompt 层信赖的不变量:**唯一**落在所有 message 块之外的 `[…]` tag 行,就是本函数发的那一行。
正文逐字节透传(不转义不截断,铁律 #16),靠 nonce 而非改内容守住边界。**关键**:nonce 不能派生自任何发送方可
影响的字段(`msg_id` 可能就是队友自己那条消息的 id),否则可预测=可伪造——故用纯随机。副作用:`render_injection`
不再是纯函数,`test_nexus_steer_pump` 对它改成结构断言(不再比对二次渲染的精确字节)。测试 `test_steer_channel`
补了真正的越狱用例(正文含 `</message>` + 假 tag,断言整段被困在单一 nonce 块内)。

**可观测**:`SteerChannel.__init__(run_id=None, agent_id=None)`——orchestrator 传两者,告警文本带 `run=/agent=`
以便 on-call 从一进程多 run 里定位是哪条 run 在超发(无参构造仅供测试)。`push` 的 qsize 告警改**沿上边触发**
(`== _STEER_INFLIGHT_WARN + 1`,32→33 那一次)而非**电平**(`> 32`):一次积压 drain 可达 `MAX_UNCONSUMED_PER_RUN`
(500),电平式会刷几百条交错日志,正好在最该读懂时淹掉信号;沿边只在跨阈值那次打一条。诊断不截断(铁律 #16);
`qsize()` 供上层节流。`_SOURCE_TAGS` 用直接下标(漏了 KeyError)+ import 期 `assert` 覆盖全 `SteerSource`。
