---
code_file: src/xyz_agent_context/channel/external_identity.py
last_verified: 2026-06-24
stub: false
---

# external_identity.py — external IM 会话的 scope 身份

## 为什么存在

身份分离版要把"每个 external IM 用户/群"当成真·per-user 租户跑全套 runtime。
这个模块定义那个身份:`external_subject_id(channel, room_id) -> "ext:{channel}:{room_id}"`。
它被 trigger 用来当 `user_id` 传给 runtime(配合 `scope_to_owner=False`,见
[[agent_runtime.py]] 的 `_resolve_scope_user_id`),于是 narrative / workspace /
executor 容器全部按这个 subject 隔离;计费仍落 owner(按 agent_id 解析,不看 subject)。

## 设计决策

**房间派生**:DM room(1:1)→ 该 room 等于那个人 → per-person scope;group room →
per-group(社区)scope。**room_id 就是判别器**,一条规则覆盖两种,不需要单独检测
DM/群。

**room_id 哈希成定长**:scope id 必须塞进 `users.user_id` **VARCHAR(64)**(全表统一的
user_id 宽度)且是文件系统安全的目录名,但 IM room id 无界(Matrix `!opaque:server` 等)
且含路径敌对字符。所以取 `sha256(room_id)[:16]`(64 bit,per-owner 规模无碰撞),channel
保留可读。原始 (channel, room_id) 在 provisioning 时写进 users 行 `metadata` 供反查。

**`ensure_external_user`(provisioning)**:幂等 get-or-create 一个**持久** `users` 行
(`user_type="external_im"`,metadata 存 channel/room_id/owner)。external 身份是一等持久
用户(非临时)。**best-effort**:provisioning 失败**不阻断 agent run**(scope 隔离只靠
user_id 字符串,行在不在都隔离),失败只 log;并发首消息竞争由 user_id UNIQUE 吸收。

**`ext:` 前缀**:让 executor/broker 能识别"这是外部 subject、不是真实平台用户"(避免对
它做真人配额/计费校验,见 deploy 侧 broker)。`is_external_subject(user_id)` 提供判定。

**空值即报错**:channel 或 room_id 为空会 `ValueError` —— 空段会把不同会话塌缩到同一
scope,是数据隔离 bug,必须早失败而非静默。

## Gotcha / 边界情况

- subject 字符串会进文件系统路径(`{user_id}/{agent_id}`)。`:` 在 POSIX 路径合法;若未来
  某个 sink 不接受 `:`,在那个 sink 处理,不要改这里的规范形式(否则 narrative 与 workspace
  的 user_id 会分叉)。
- 用 raw room_id(不做 sanitize)是**故意**的:sanitize 可能让两个不同 room 撞成同一
  subject → 跨租户数据泄露。raw id 无碰撞。
