# 邀请码机制 — PRD + 实现方案

- **Trigger**：用户要为 NarraNexus web version 做邀请码机制，目的是控制注册用户数量
- **Branch / commit**：`main` @ `b4aa5c4`
- **Status**：implemented + pushed（见文末「## 更新 2026-05-14 — 实现完成」）；唯一未决：正式 SMTP 供应商（不阻塞，先用个人 Gmail）
- **核心结论**：用 DB `invite_codes` 表替换现有的单一全局 `INVITE_CODE` 环境变量；Mode B（自动发码 + 全局上限 + waitlist），上限 200；先控用户数，不控 agent 数。

---

## 1. 背景与现状

当前 `backend/auth.py` 是**单一全局 `INVITE_CODE` 环境变量**：所有人共用一个码，`register()` 里 `request.invite_code != INVITE_CODE` 即拒绝。问题：无法追踪、不能撤销、不能限量、一处泄漏全线失守。

按铁律 #2（不做向后兼容），整个替换成基于数据库的唯一邀请码机制。

## 2. 需求（已确认）

| 需求 | 决策 |
|---|---|
| 邀请码唯一 | DB `unique` 约束 + 单次消费（用后即焚） |
| 可从 website 申请 | website 申请页 + 后端 public endpoint `/api/invite/request` |
| 用户填邮箱，我们发码 | 新增 mailer 工具，申请时自动生成并发邮件 |
| 用后标记 used | `register()` 里原子条件 UPDATE 消费 |
| **控量模式** | **Mode B：自动发码 + 全局上限 + waitlist** |
| **上限** | **200**（`INVITE_AUTO_ISSUE_CAP=200`） |
| **控什么** | **先控注册用户数**，不控 agent 数（一个用户仍可建多个 agent；如需卡 agent 总量另开 PRD 做 per-user agent 上限） |

## 3. 控量模式：Mode B

`/request` 时一律生成 code：
- 当前 `status ∈ {issued, used}` 的行数 < 200 → 状态 `issued`，发邮件
- ≥ 200 → 状态 `waitlisted`，**不发邮件**
- 管理员放行：`waitlisted → issued` 并补发邮件

表结构同时兼容 Mode C（审批队列），以后想加审批不用改表。

## 4. 数据模型

新增表 `invite_codes`，注册进 `utils/schema_registry.py`：

| 列 | 类型 (sqlite / mysql) | 说明 |
|---|---|---|
| `id` | INTEGER / BIGINT UNSIGNED PK auto | |
| `code` | TEXT / VARCHAR(32) **unique** | 邀请码本体，如 `NX-7K9MQ2WX` |
| `email` | TEXT / VARCHAR(255) | 申请邮箱 |
| `status` | TEXT / VARCHAR(16) | `issued` \| `used` \| `waitlisted` \| `revoked` |
| `source` | TEXT / VARCHAR(32) | 申请来源，默认 `website` |
| `email_sent` | INTEGER / TINYINT | 邮件是否真发出去了 |
| `created_at` | TEXT / DATETIME(6) | |
| `issued_at` | TEXT / DATETIME(6) nullable | 转为 issued / 邮件下发时间 |
| `used_at` | TEXT / DATETIME(6) nullable | 被注册消费的时间 |
| `used_by_user_id` | TEXT / VARCHAR(128) nullable | 谁用的 |

索引：`unique(code)`、`idx(email)`、`idx(status)`。

**状态流转**：`request` 生成 code → 未超 cap 则 `issued`（发邮件），超 cap 则 `waitlisted`（不发）→ 管理员可 `waitlisted→issued`（补发）→ 注册消费 `issued→used` → 管理员可 `→revoked`。

## 5. 邀请码生成算法（current design v1 — 后续有优化空间）

> 用户决定：本节为**当前设计 v1**，先按此实现跑通；生成算法后续有优化空间（如更长字符空间、分段校验位、可读性分组等），不在 v1 范围内。

### 结论：CSPRNG 随机短串 + DB unique 约束兜底，**不用** snowflake / 号段模式

用户参考的 juejin 文章（7137315582822580238）讲的是**分布式高并发发号器**（snowflake / 美团 Leaf 号段模式）——解决的是"每秒百万级、有序、多节点不冲突"的数字 ID。邀请码的需求恰好相反：

| 维度 | 发号器（juejin） | 邀请码 |
|---|---|---|
| 量级 | 百万/秒 | 总共几百个 |
| 是否要有序 | 要 | **不能要**——有序=可枚举 |
| 是否要人可输入 | 不要（64-bit long） | **要**（短、无歧义字符） |
| 是否要不可猜 | 不要 | **必须**——可猜=200 上限形同虚设 |

所以发号器是错的工具。**自增 ID / sequence 同样不行**（顺序可枚举）。**UUID4** 唯一且随机但 36 字符太长、不好手输。

### 算法

1. **生成**：`secrets.choice`（CSPRNG，不是 `random`）从安全字母表取 8 位。
   - 格式：`NX-` + 8 位
   - 字母表（去掉 `0 1 I L O U` 等易混字符）：`23456789ABCDEFGHJKMNPQRSTVWXYZ`（约 30 字符）
   - 空间 ≈ 30^8 ≈ 6.5×10^11 ≈ 2^39
2. **落库**：`INSERT`，靠 DB 的 `unique(code)` 约束做权威保证。
3. **碰撞重试**：捕获 unique violation → 重新生成 → 重试，最多 5 次。

### "DB 提供还是算法提供"——两者都要，各管一段

- **算法（CSPRNG）**负责：不可猜性 + 格式 + 人可输入
- **DB unique 约束**负责：唯一性的权威保证 + 并发 race 兜底

碰撞概率（生日问题）：200 个码在 2^39 空间里 ≈ 200² / (2 × 5.5×10^11) ≈ 3.6×10^-8，可忽略；即便涨到 1 万个码也才 ~9×10^-5。加上 DB 约束 + 重试，正确性与概率无关——一定不会发出重复码。

## 6. 接口设计

**Public（无需登录）**
- `POST /api/invite/request` — body `{ email }`
  - 幂等：同邮箱已有 `used` → "该邮箱已注册，请直接登录"；已有 `issued` → **重发同一个码**（不新生成，防刷）；已有 `waitlisted` → 提示排队中；否则新建
  - 防刷：复用 `backend/routes/_rate_limiter.py` 的 `SlidingWindowRateLimiter`，按 IP + 按 email 双限流
  - 返回 `{ success, status, message }`，**响应里绝不回显 code**（code 只走邮件，否则限流形同虚设）

**注册改造（`POST /api/auth/register`）**
1. 先 `SELECT` 校验 code 存在且 `status='issued'`（快速报错给 UX）
2. 校验密码/用户名、检查 user 不存在
3. **原子消费**：`UPDATE invite_codes SET status='used', used_at=…, used_by_user_id=… WHERE code=? AND status='issued'`，校验 affected rows == 1（挡住并发抢码）
4. 消费成功后 insert user；若 insert 失败 → 把 code revert 回 `issued`

**Admin（复用现有 admin secret key 鉴权，参照 `backend/routes/admin_quota.py`）**
- `GET /api/admin/invite/codes` — 列表 + 按 status 过滤
- `POST /api/admin/invite/promote` — `waitlisted → issued` + 补发邮件
- `POST /api/admin/invite/revoke` — 撤销

## 7. 邮件发送（SMTP 供应商 — 待用户拍板）

### 可以先用个人邮箱试验吗？可以。

因为我们做的是 **mailer 抽象层 + SMTP 默认实现**，"现在用个人 Gmail → 以后换正式供应商"只是改环境变量，代码不动。

### 建议路径

| 阶段 | 方案 | 说明 |
|---|---|---|
| **开发 / 内测（现在）** | **个人 Gmail SMTP** | `smtp.gmail.com:587`，用 Gmail **App Password**（不是登录密码）。零成本、立即可用。限制：~500 封/天（200 用户内测足够）、发件人显示为你的个人邮箱 |
| **正式上线** | **Resend** 或 **AWS SES** | Resend：API 最干净、免费额度大、送达率好。SES：规模化最便宜，但要验证域名 + 申请退出 sandbox |

### 上线前必做（不论选谁）

用验证过的域名（`narra.nexus`）发信，配好 **SPF / DKIM / DMARC**，否则邀请邮件大概率进垃圾箱。个人 Gmail 内测阶段可以跳过，正式上线必须做。

### 待确认

正式供应商用哪个？（Resend / AWS SES / 公司 SMTP）—— 不阻塞开发，先用个人 Gmail 把链路跑通。

## 8. 各层改动清单

**后端（NarraNexus）**

| 层 | 文件 | 改动 |
|---|---|---|
| Schema | `utils/schema_registry.py` | 新增 `invite_codes` TableDef（改） |
| Schema | `schema/api_schema.py` | 新增 `InviteRequestRequest/Response`、`InviteCode` 实体（改） |
| Repository | `repository/invite_code_repository.py` | `InviteCodeRepository`：`get_by_code/get_by_email/create/consume/count_active/list_all/revoke`（新） |
| Util | `utils/mailer.py` | 邮件抽象层 + SMTP 默认实现（新）。按铁律 #9，抽象接口 + SMTP 默认，不硬绑供应商 |
| Util | `utils/invite_code_gen.py` | 邀请码生成器（CSPRNG + 安全字母表 + 重试）（新） |
| Route | `backend/routes/invite.py` | public `/api/invite/request`（新） |
| Route | `backend/routes/admin_invite.py` | admin list/promote/revoke（新） |
| Route | `backend/routes/auth.py` | `register()` 改造（改） |
| Auth | `backend/auth.py` | 删除 `INVITE_CODE` 常量（改） |
| Bootstrap | `backend/main.py` | 注册两个新 router；CORS 允许 website 域（改） |
| Config | settings / `.env` 示例 | 删 `INVITE_CODE`；加 `SMTP_HOST/PORT/USER/PASSWORD/FROM/TLS`、`INVITE_AUTO_ISSUE_CAP=200`（改） |
| Tier-2 | 每个新文件配套 `.mindflow/mirror/…md`；改动文件刷新对应 mirror md（铁律 #10） | |

**App 前端（NarraNexus/frontend）**：基本不动——`RegisterPage.tsx` 已有 `invite_code` 输入框，错误文案微调即可。

**Website（narranexus-website，独立 repo）**：
- `app/invite/page.tsx` — 申请页：邮箱输入 + 提交 + 结果提示（新）
- `app/api/invite/route.ts` — Next route handler 代理转发到 NarraNexus 后端（新）。走代理而非直连：避免 CORS、藏后端 endpoint、可在这层加 Cloudflare Turnstile / honeypot 挡机器人
- 首页/导航加入口链接

## 9. 关键设计点 & 边界 case

- **原子消费**：条件 UPDATE + affected-rows 校验，挡两人抢同一码；insert user 失败要 revert code
- **防刷**：IP + email 双限流；一个邮箱永远只对应一个有效码（重复申请重发旧码）
- **不回显 code**：`/request` 响应绝不带 code
- **邮件失败**：`email_sent=0` 标记，`/request` 仍返回成功（码已生成），管理员可在列表里看到未送达的重发；邮件失败不阻断流程
- **铁律 #7 双运行方式**：local（SQLite）模式本来就 bypass 注册，邀请码只对 cloud 生效，维持 `_is_cloud_mode()` 分支，不影响桌面端
- **铁律 #2**：`INVITE_CODE` 环境变量彻底删除，不留兼容分支

## 10. 复杂度（结构性维度，铁律 #17）

- 新文件 7 个（mailer / code-gen / repo / 2 route / 2 website），改动文件 ~6 个 + 同等数量 mirror md
- 涉及 4 层：schema / repository / route / util，外加 website 一个独立 repo
- 前置依赖：确定 SMTP 供应商和凭证（**不阻塞**——先用个人 Gmail）
- 风险等级：中。新增表可独立回滚；唯一不可逆点是 `INVITE_CODE` 删除后旧部署环境变量失效——cloud 重新部署即可

## 11. 测试覆盖（铁律 TDD）

- 单测：code 生成唯一性/字母表合法性、`consume` 原子性（并发两次只成功一次）、幂等申请（同邮箱重发旧码）、cap 边界（第 201 个转 waitlist）
- 集成：`/invite/request` → 收码 → `/register` 消费 → 再次 `/register` 同码失败
- mailer：mock SMTP，验证发送失败不阻断 `/request`

## 12. Next step

- [ ] 用户拍板正式 SMTP 供应商（不阻塞，先用个人 Gmail 跑通链路）
- [x] 进入实现：按第 8 节逐文件做，先写测试（TDD）
- [x] 实现前 Read 对应 mirror md（铁律 #3）
- [x] website 改动在 `narranexus-website` 独立 repo，单独 commit
- [ ] 已确认范围：只控用户数。若后续要控 agent 总量 → 另开 per-user agent 上限 PRD

---

## 更新 2026-05-14 — 实现完成

**Branch**：两个 repo 都推到 `invitation_code_2026_05_14`
- NarraNexus：commit `454922e`
- narranexus-website：commit `a70de9d`

**Status**：done（待用户配 SMTP + 自测）

### 落地内容

后端（NarraNexus）：
- `invite_codes` 表（`schema_registry.py`）+ `InviteCode` schema
- `invite_code_gen.py`（CSPRNG 生成器）+ `mailer.py`（通用 SMTP，stdlib，未配置即 no-op）
- `InviteCodeRepository`：`consume` 是单条带条件 UPDATE，并发抢码的 race guard
- `POST /api/invite/request`（public，JWT 豁免）：幂等 + IP/email 双限流 + Mode B cap(200)
- `/api/admin/invite` list/promote/revoke（staff JWT）
- `register()` 改为校验 + 原子消费 DB 码，user insert 失败回滚码
- 删除全局 `INVITE_CODE` 环境变量
- 全部 mirror md 同步（铁律 #10）

website（narranexus-website）：
- `/invite` 申请页 + `/api/invite` 服务端代理路由 + header "Request Access" 导航项

### 取证记录（关键文件）

- 表定义：`src/xyz_agent_context/utils/schema_registry.py`（`invite_codes`）
- 原子消费：`src/xyz_agent_context/repository/invite_code_repository.py::consume`
- 注册改造：`backend/routes/auth.py::register`
- cap 配置：`backend/config.py::Settings.invite_auto_issue_cap`
- 测试：`tests/{utils,repository,backend}/test_invite*` —— 26 个新测试全过，
  `tests/{backend,repository,schema,utils}` 全套 190 passed / 3 skipped 无回归
- ruff 干净；pyright 仅报 4 个 auth.py **既有** issue（login/update_agent/
  delete_agent），新文件 0 error

### Next step（待用户）

- 配置 SMTP 环境变量（先个人 Gmail App Password 跑链路）：
  `SMTP_HOST/SMTP_PORT/SMTP_USER/SMTP_PASSWORD/SMTP_FROM`
- 云端部署设 `INVITE_AUTO_ISSUE_CAP`（默认已是 200）、删掉旧的 `INVITE_CODE` 环境变量
- website 部署设 `NARRANEXUS_API_URL`（默认 `https://agent.narra.nexus`）
- 自测：申请 → 收码 → 注册 → 重复码被拒；cap 触发 waitlist
- 两个 repo 的 PR review + merge
- 邀请码生成算法后续优化（见 §5，v1 已够用）
