---
code_file: backend/routes/admin/warn.py
last_verified: 2026-08-19
stub: false
---

# admin/warn.py — 敏感操作即时警告端点

## 2026-08-19 — M1/M2：dedup 读收窄 + 幂等措辞如实

- **M1**：幂等 dedup 读只需最新一行的时间戳，改为 `db.get(..., limit=1,
  order_by="created_at DESC", fields=["created_at"])`——不再全行拉回一个话痨用户所有
  `abuse_warning` 历史（`payload` 是 MEDIUMTEXT，全行扫描把整条通知体拖回来纯属浪费）。
- **M2**：dedup 是 read-then-write、**无唯一约束兜底**，并非原子。原 docstring 声称
  「a network retry cannot double-notify」是假的（两个真正并发的 POST 会双双通过检查、双双
  插入）。改为如实措辞：它只收敛**顺序**重试；并发下最坏多发一条泛化警告通知——无害（不同于
  重复的处置行），故不为它付一个唯一索引的代价。

## 为什么存在

反滥用处置阶梯里，「计分」与「硬处置（封禁四件套）」之间需要一层**渐进响应前置**：
当私有 sentinel 检测到「可疑但未坐实」的软信号敏感操作（如裸隧道接触
`tunnel_contact`，`score_delta=0`），除累计风险分外，向该用户投递**一条**固定泛化的
系统警告，作为温和的前置干预。本路由就是这层投递的落点：

- `POST /api/admin/warn-user` —— 写一行 `user_notifications`（`kind="abuse_warning"`）
  + 一行 `ban_audit`（`action="warn"`）审计

## 这个文件不做什么

**不接受、也不回显任何规则细节。** user-facing 文案是模块内的**固定英文常量**
`SENSITIVE_OP_WARNING`，无论收到什么输入都写这一句——检测规则 / 阈值 / 证据绝不
经用户消息泄漏（信任边界硬约束：措辞泛化不暴露规则）。调用方传入的 `category` 是
**opaque**，只进 `ban_audit.reason`，**永不**进用户通知。精细的「哪条规则、什么证据」
由 sentinel 侧 incident ledger 另行落库，无需进用户消息。

**不解析文本、不做归因。** 端点只收 `user_id`（sentinel 侧已校验 32-hex 并做白名单
豁免），本端点不重复判白名单、不从日志/容器名取归因，只按传入 `user_id` 查用户。

**不做处置、不减分。** warn 只是投递一条通知 + 留痕，不改 `users.status`、不动风险分
阶梯。

## 上下游关系

**被谁用**：
- deploy 侧私有 sentinel 的 `WarnEnforcer`/`WarnApiClient` 转发（带 `X-Admin-Secret`，
  只传 `user_id` + 固定粗粒度 opaque `category="sensitive_operation"` + `actor`）。
- `backend/main.py`：`app.include_router(admin_warn_router, tags=["AdminWarn"])`，
  router 自带 prefix `/api/admin`，最终挂载 `POST /api/admin/warn-user`。
- 路径 `/api/admin/warn-user` 在 [[auth]] 的 `AUTH_EXEMPT_PATHS` 里（自凭证、机器
  调用、无用户 JWT，与 [[suspend.py]] / [[gateway_key_misuse.py]] 同理；否则 middleware 先 401、
  端点不可达）。

**依赖谁**：
- `._admin_secret.require_admin_secret`：**共享**的 admin secret 校验 helper（与
  [[suspend.py]] / [[gateway_key_misuse.py]] / [[migration.py]] / [[runtime.py]] 同一把
  `admin_secret_key`，常量时间比较，见 [[_admin_secret.py]]）。本模块保留
  `from xyz_agent_context.settings import settings` 再导出，只为测试能用 `mod.settings`
  覆盖 secret。
- [[user_repository]]：`UserRepository(db).get_user(user_id)` 查用户存在性 + 取
  `prev_status`（未知用户 → 404）。
- [[ban_audit_repository]]：`BanAuditRepository(db).record(user_id, ACTION_WARN, ...)`
  写审计（best-effort，与 suspend/reinstate 共表）。
- `user_notifications` 表（见 [[schema_registry.py]]，self-heal 首创的投递面）：直接
  `db.insert` 一行 `kind="abuse_warning"`、`severity="warning"`、`payload` 为固定
  `{"code","message"}` JSON。
- `xyz_agent_context.utils.db.db_factory.get_db_client`：取全局 async DB client。
- `xyz_agent_context.utils.timezone.utc_now`：dedup 窗口计算基准。

## 设计决策

- **X-Admin-Secret 替代 JWT**：能给用户推系统警告的输入是高危面，普通 JWT 不作为凭证；
  且调用方是无 JWT 的内部机器（sentinel）。未配置 secret → 503、header 缺失或错误 →
  403，语义与 suspend/gateway_key_misuse 完全一致（共享 helper）。executor/agent 无此凭据。
- **文案固定泛化 + `code` 供前端本地化**：代码禁中文串（铁律 #1），故 `message` 为固定
  英文常量、另给 `code="sensitive_operation_warning"` 让前端 bell 本地化。这也是信任
  边界要求——端点无论收到什么 category 都写同一句。
- **端点自带幂等（`_DEDUP_WINDOW_SEC=6h`）**：投递前查该 user 最近一条 `abuse_warning`，
  若 `created_at` 在窗内则短路返回 `already=True` 且不重复写行/审计。这一层独立于
  sentinel 侧的 durable 台账，专防网络重试导致的重复投递（两层去重）。6h 与 sentinel
  滑窗对齐。
- **`created_at` 归一化 `_as_utc`**：DB facade 有的后端回 `datetime`、有的回 SQLite
  的 ISO 字符串；`_as_utc` 两者都收，naive 当 UTC，无法解析的行**忽略**（fail-safe：
  最坏多发一条，绝不 crash 或误判为「已发」而漏发）。
- **写 notification 直接 `db.insert`、写审计走 repository**：通知行是普通投递（与
  self-heal 同模式，非权威处置输入），审计走既有 `BanAuditRepository`。分层上 route 不
  拼 SQL 的承重写（审计）走 repository，通知复用现成表的既定写入形态。

## Gotcha / 边界情况

- **触发**：`settings.admin_secret_key` 未配置 → **症状**：端点 503 → **根因**：共享
  `require_admin_secret` 在 expected 为空时直接 503（防空 secret 匹配空 header 绕过）。
- **触发**：忘了把 `/api/admin/warn-user` 加进 `AUTH_EXEMPT_PATHS` → **症状**：
  middleware 先 401、端点根本不可达（gateway_key_misuse 端点批次的教训）→ **根因**：默认路径走用户
  JWT 鉴权，自凭证端点必须显式豁免。
- **触发**：窗内二次 warn → **症状**：`already=True`、不重复写通知/审计 → **根因**：
  端点侧幂等短路。
- **触发**：请求夹带额外字段 → **症状**：被 pydantic 丢弃 → **根因**：`WarnRequest` 只
  声明 `user_id` / `category` / `actor`。

## 命名 / 中性纪律

对外一律以「敏感操作即时警告」的通用泛化语汇描述，不含检测策略、特征、规则 id 或识别
逻辑。`category` 是调用方传入的不透明取证类别，本层不赋予含义、只落审计。
