---
code_file: src/xyz_agent_context/agent_runtime/steer_channel.py
last_verified: 2026-08-21
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

## 2026-08-21(补)— review 加固:防伪造渲染 + 在飞 queue 可观测

`render_injection` 把来源 tag 和用户原文**结构分离**(tag 独占一行 + 正文放 `<message>` 块),队友正文里含
`[the owner adds]` 也伪造不了平台的 owner tag(owner/teammate 是真权限区分);仍纯追加 user 消息。`_SOURCE_TAGS`
用直接下标(漏了 KeyError)+ import 期 `assert` 覆盖全 `SteerSource`。`push` 在 qsize 超 `_STEER_INFLIGHT_WARN`
(32)时 `logger.warning`——无界 queue 的不变量变可观测(诊断不截断,铁律 #16);`qsize()` 供上层节流。
