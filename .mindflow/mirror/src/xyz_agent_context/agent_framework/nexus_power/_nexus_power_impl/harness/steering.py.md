---
code_file: src/xyz_agent_context/agent_framework/nexus_power/_nexus_power_impl/harness/steering.py
last_verified: 2026-08-21
stub: false
---
# harness/steering — 插话入口

DRAIN_STEERING 调用点 day-1 在。两个实现:`NullSteeringInlet` 恒空(默认,测试锁定);`QueueSteeringInlet` 是 P4 TriggerInbox 落地——背一个进程内 asyncio.Queue,transport 层(本地 runner stdin reader / 云端 executor steer 端点)是唯一写入者,loop 只在 step 边界 `drain()`,producer(team/单聊)永不触碰 loop。drain 是快照即清空、绝不阻塞(空 inlet 是常态);顺序=队列 FIFO。注入只许纯追加(C2,护 prompt cache 前缀)。此文件只管 inlet;喂队列的 transport 与融合路由在别处(§见 live-steering 设计)。

## 2026-08-21(补)— 写入端契约:back-pressure 在 steer_inbox,不在这条 queue

QueueSteeringInlet drain 的那条 queue 是**已准入消息的在飞交接**,transport(`SteerChannel`)**留它无界**、`put_nowait` 从不阻塞;有界/back-pressure 落在上游 `steer_inbox` 写边界(producer 超速被 `SteerInboxFull` 挡,绝不丢,铁律 #16)。防这条在飞 queue 涨爆的不变量是 **orchestrator 的**:必须按 loop 的 drain 速率(一个 step 边界的量)往 channel 推,不能一次把 inbox 积压全 drain 进来——这条节奏由 steer 路由 PR 保证。
