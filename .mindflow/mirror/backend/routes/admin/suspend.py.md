---
code_file: backend/routes/admin/suspend.py
last_verified: 2026-08-13
stub: false
---

# admin/suspend.py — 账户停用（account suspension）HTTP 端点

## 为什么存在

平台需要一个通用、可复用的开关，用来把某个用户账户的状态在「可交易 / 已停用」之间切换，并留下一条中性的操作审计。本路由提供三个自凭证的运维接口：

- `POST /api/admin/suspend` —— 把账户状态置为已停用（写 `users.status = banned`）
- `POST /api/admin/reinstate` —— 把账户状态恢复为 `active`
- `GET  /api/admin/account-state/{user_id}` —— 读取账户当前状态

三者都用 `X-Admin-Secret` header 校验 `settings.admin_secret_key`（与 [[migration.py]] 的 migrate-identity 同一自凭证模式），驱动方是私有运维方而非用户 JWT。

独立成一个路由文件（而非塞进 `admin/quota.py` 或 auth 路由），是因为它改写的是账户主体的**可用性状态**，鉴权模式、调用者、风险等级都与额度管理或普通登录完全不同。铁律 #3（模块独立）。

## 这个文件不做什么

**它不持有任何策略（policy-free）。** 它不判断谁该被停用、也不判断为什么——那属于外部私有调用方。`reason` / `evidence_ref` 是**不透明的自由文本**，本层原样转交给审计层，绝不解析、分类或校验。本文件对「一个账户如何走到停用状态」一无所知，只提供切换开关本身。

不遍历用户批量停用；批量由调用方逐条 POST 驱动。不做任何检测、打分或识别工作——这些概念不存在于本层。不保证 `ban_audit` 审计行一定落库（审计是 best-effort，见下）。

## 上下游关系

**被谁用**：
- 私有运维方 / 内部工具：带 `X-Admin-Secret` 调用三个端点，切换或查询账户状态。
- `backend/main.py`：`app.include_router(admin_suspend_router, tags=["AdminSuspend"])`，router 自带 prefix `/api/admin`。
- 三条路径都在 [[auth]] 的豁免名单里：两个 POST 进 `AUTH_EXEMPT_PATHS`，路径参数形式的 GET 读端点进 `AUTH_EXEMPT_PREFIXES`（`/api/admin/account-state/`）。

**依赖谁**：
- `xyz_agent_context.repository.user_repository.UserRepository`：读用户、写 `users.status`。
- `xyz_agent_context.repository.ban_audit_repository.BanAuditRepository`（+ `ACTION_SUSPEND` / `ACTION_REINSTATE` 常量）：写审计行。
- `xyz_agent_context.schema.UserStatus`：状态枚举（`BANNED` / `ACTIVE` 等）。
- `xyz_agent_context.settings.settings`：读 `admin_secret_key`。
- `backend.auth.invalidate_account_state`：**惰性 import**，停用/恢复后清掉 middleware 的账户状态缓存，让改动在本进程内立即可见。

## 设计决策

- **X-Admin-Secret 替代 JWT**：能停用任意账户是高危操作，普通 JWT（含 staff 角色）不作为凭证。而且被停用的账户手里也没有可用 JWT，用用户认证路径去 gate 这几个端点本身就是循环依赖。未配置 secret → 503（视为「功能未启用」的误配置，宁可拒绝也不敞开），header 缺失或错误 → 403。与 migrate-identity、runtime/status 一致。
- **`suspend` 幂等**：`_SUSPENDED_STATES = {BANNED, BLOCKED, DELETED}`。若账户已处于任一「不可交易」状态，则不再写 `users.status`，直接返回 `already=True` 的成功。`banned` 是本机制专属的值；`blocked` / `deleted` 是既有的终态，同样算作「已停用」不重复置位。
- **审计每一次调用都写，包括幂等 no-op**：即使状态没变，也记一行审计——让「谁在何时请求过」可追溯。审计写入通过 `BanAuditRepository.record`，是 best-effort，永不把异常抛回本路由（丢一行审计不能让运维请求失败）。
- **停用/恢复后清缓存**：`_invalidate_cache` 惰性 import `backend.auth.invalidate_account_state`，best-effort 清掉 middleware 的 30s TTL 账户状态缓存，让本进程内立即生效。惰性 import 是为了让本路由在 import 期不拉入 auth middleware（也方便测试单独跑本路由）；清缓存失败只记 WARNING，不影响主流程（跨进程 staleness 由 TTL 兜底）。
- **`banned` 作为 suspend 写入值**：`suspend_account` 统一写 `UserStatus.BANNED`，与既有的 `blocked` / `deleted` 区分开，使本机制有自己的专属状态值，`reinstate` 只需把行恢复成 `ACTIVE`。

## Gotcha / 边界情况

- **触发**：`settings.admin_secret_key` 未配置 → **症状**：三个端点全部 503 → **根因**：`_require_admin_secret` 在 expected 为空时直接 503，防止空 secret 匹配空 header 绕过鉴权。
- **触发**：对一个已 `banned`/`blocked`/`deleted` 的账户再次 `suspend` → **症状**：返回 `suspended=True, already=True`，`users.status` 不变，但仍落一行审计 → **根因**：幂等成功语义 + 审计记录每一次调用。
- **触发**：`suspend` 成功但 `ban_audit` 写失败 → **症状**：`users.status` 已改、接口正常返回，但审计缺一行（只在日志里留 WARNING）→ **根因**：`users.status` 是真相源，审计是 advisory 旁路，刻意不因审计失败回滚状态变更。
- **触发**：`invalidate_account_state` 清缓存失败，或停用发生在另一个 backend 进程 → **症状**：被停用账户可能在最多约 30s 内继续用已签发的 JWT 交易 → **根因**：middleware 账户状态缓存的 TTL 上界；这是刻意的「可用性优先」权衡（见 [[auth]]）。

## 命名 / 中性纪律

本模块对外一律以「账户停用 / moderation」的通用语汇描述，不含任何检测策略、特征或识别逻辑。`reason` / `evidence_ref` 是外部调用方设置的不透明字符串，本层不赋予它们任何词汇含义。
