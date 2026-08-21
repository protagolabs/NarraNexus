---
code_file: src/xyz_agent_context/agent_framework/nexus_power/_nexus_power_impl/harness/steering.py
last_verified: 2026-08-21
stub: false
---
# harness/steering — 插话入口

DRAIN_STEERING 调用点 day-1 在。两个实现:`NullSteeringInlet` 恒空(默认,测试锁定);`QueueSteeringInlet` 是 P4 TriggerInbox 落地——背一个进程内 asyncio.Queue,transport 层(本地 runner stdin reader / 云端 executor steer 端点)是唯一写入者,loop 只在 step 边界 `drain()`,producer(team/单聊)永不触碰 loop。drain 是快照即清空、绝不阻塞(空 inlet 是常态);顺序=队列 FIFO。注入只许纯追加(C2,护 prompt cache 前缀)。此文件只管 inlet;喂队列的 transport 与融合路由在别处(§见 live-steering 设计)。
