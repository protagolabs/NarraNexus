---
code_file: src/xyz_agent_context/schema/channel_ingress_breaker_schema.py
stub: false
last_verified: 2026-08-24
---
# channel_ingress_breaker_schema.py — ingress 熔断器的持久状态模型

## 为什么存在

[[ingress_guard.py]] 的落库那一半。每个会话键一行。

`session_key(channel, chat_id, sender_id)` 是**单一定义**，放在这里而不是
guard 里：内存缓存、DB 行、audit 轨迹三处必须用同一把钥匙指同一个对话，
各拼各的是它们对不上的开始。

## 设计决策

**只有层级变迁落库。** 驱动这些变迁的滑窗计数和内容指纹留在进程内存里——
为 10 分钟就过期的数据每条消息写一行是纯写放大。必须活过重启的是**冷却**：
8/14 那个循环跑了 70+ 小时，期间任何一次重新部署都会把一个已经被隔离 24
小时的对端重新放行，事故就在平台「从没见过这个人」的认知下继续。

**`tier` 是升级记忆，跨发布保留**（同 [[channel_trigger_base.py]] 的
`_breaker_release` 对凭据熔断器的处理）：清完冷却立刻再犯的会话必须落到
schedule 的**下一档**，不是从最短那档重来。

**`suppressed_count` 每次跳闸清零**，所以这个数回答的是「**这一次**隔离
挡下了多少」，而不是一个没有意义的终身累计——它是 owner 通知里的头条数字。

铁律 #14/#15：这张表管的是**入站**准入。它不封顶、不取消、不给运行中的
`agent_loop` 设时限，也不评判 agent 自己的模型或输出，只看进来的流量形状。
