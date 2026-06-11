# Phase 1 设计：用户管理系统统一与迁移

> 日期：2026-06-10
> 状态：设计稿（待 Bin 哥确认后进入 plan/实现）
> 前置阅读：`../research/00-synthesis-and-decisions.md`（决策 D1/D2/D3/D7/D8 均已定）
> 实现仓库：NarraNexus（仓库 A）+ NarraNexus-deploy（仓库 B，仅 env/文档）

---

## 1. 目标与范围

**目标**：cloud 模式的用户注册/登录全面替换为 NetMind.AI 账号体系；完成存量用户迁移；准入闸门从「邀请码注册」迁移为「可配置的额度发放策略」。

**范围内**：
- 后端 NetMind auth client + `netmind-login` 端点 + users 惰性 upsert
- 删除自建密码登录/邀请码注册/开放 create-user（cloud）
- 前端 NetMind 登录组件（移植 Arena Login 套件）+ 双 token 管理
- 额度发放：**首次登录（注册）即送免费额度**（2026-06-10 拍板：邀请码机制整体取消）
- 存量用户一次性迁移脚本
- smoke test 用户 fixture 的 dev-bypass 机制

**范围外**（后续 phase）：Arena 跳转适配（Phase 2，但本设计为它预留 `source` 参数位）、充值/credits/api-key 自动生成（Phase 3，但预留 NetMind token 留存位）。

**不变量**：
- local/桌面模式认证完全不动（X-User-Id 机制、create-user local 入口、无密码登录）
- 自家 JWT 基础设施保留（`JWT_SECRET`、HS256、auth_middleware、WS 认证、22 个路由文件全部零改动）
- 数据库无危险变更（铁律 6）：不缩窄/不改列类型

---

## 2. 架构

```
┌─ 浏览器（NarraNexus 前端，cloud 模式）─────────────────────┐
│ LoginPage（新）：内嵌 NetMind 登录                          │
│   邮箱密码 → POST {NETMIND_AUTH_API}/user/emailLogin       │
│   OAuth → 弹窗 {NETMIND_ACCOUNTS}/auth.html → userCallBack │
│   ↓ 拿到 netmind loginToken（JWT）                          │
│ POST /api/auth/netmind-login { netmind_token }              │
│   ↓ 返回 { user_id, token(自家JWT), role, is_new_user }     │
│ configStore: token(自家) + netmindToken(留存) 双 token      │
└─────────────────────────────────────────────────────────────┘
                          │
┌─ backend ──────────────────────────────────────────────────┐
│ routes/auth.py: POST /api/auth/netmind-login                │
│   → netmind_auth_client.verify(token)                       │
│       POST {NETMIND_AUTH_API}/user/balance                  │
│       header: token: Bearer <jwt>（注意 header 名是 token） │
│       成功 → { email, user_system_code, nickname, ... }     │
│   → UserRepository.upsert_netmind_user(...)                 │
│       user_id = user_system_code（首次 INSERT，之后 UPDATE）│
│   → 首次创建时直接种免费额度（init_for_user）                │
│   → create_token(user_id, role) 签自家 JWT                  │
│ 之后所有请求走现有 auth_middleware，无任何改动               │
└─────────────────────────────────────────────────────────────┘
```

**会话模型**（D1）：NetMind token 只在两个时刻被使用——登录瞬间（后端验证）和未来 Phase 2/3 的显式动作（前端留存的 netmindToken 随特定请求携带）。服务端**不持久化** NetMind token（它 15 天过期且换设备登录即作废，存了也不可靠，还多一份泄漏面）。

---

## 3. 后端设计

### 3.1 新模块：NetMind auth client

位置：`src/xyz_agent_context/services/netmind_auth_client.py`（与 module 解耦的平台 service 层，铁律 3：不进任何 module）。

```python
class NetmindAuthClient:
    """Verify NetMind login tokens and resolve user identity.

    NetMind JWTs cannot be verified offline (per-user signing factor
    lives in NetMind's DB), so verification delegates to the
    /user/balance endpoint, mirroring Arena's integration.
    """

    async def verify_token(self, token: str) -> NetmindUser:
        # POST {base_url}/user/balance
        # headers: {"token": f"Bearer {token}",
        #           "Content-Type": "application/x-www-form-urlencoded"}
        # timeout: 5s; raise NetmindAuthError(401) on invalid,
        # NetmindUpstreamError(502) on network/5xx
        ...

@dataclass
class NetmindUser:
    user_system_code: str   # 32-hex, becomes our user_id
    email: str              # lowercased + trimmed
    nickname: str | None
    avatar_url: str | None
    raw: dict               # full user object for forward-compat
```

配置（env，部署侧同步加到 `.env.example`）：

| env | prod 值 | test 值 | 说明 |
|---|---|---|---|
| `NETMIND_AUTH_API_URL` | `https://auth-api.netmind.ai` | `https://userauth.protago-dev.com` | 后端验证用 |
| `NETMIND_AUTH_TIMEOUT_SECONDS` | `5` | 同 | |
| `NETMIND_DEV_BYPASS` | 不设 | `1` | 见 3.7 |

注意事项（来自调研的坑）：
- header 名是 **`token`**，不是 `Authorization`（NetMind 约定，netmind.ts:40）。
- `/user/balance` 返回 `data.user` 里同时有 `email` 和 `userSystemCode`（UserRender 字段）；以响应体为准，不解析 JWT payload 做身份（JWT 的 `aud` 语义在 Arena 里是 power user id，留作交叉校验日志即可）。
- 响应里会带回 `loginToken` 等敏感字段（UserRender 不过滤它）——client 层只取需要的字段，**不落日志**。

### 3.2 路由变更（`backend/routes/auth.py`）

**新增 `POST /api/auth/netmind-login`**（加入 `AUTH_EXEMPT_PATHS`）：

```
Request:  { "netmind_token": str, "source": str | None }   # source 为 Phase 2 预留
Response: { "success": true, "user_id": str, "token": str, "role": str,
            "is_new_user": bool, "quota_granted": bool }
```

流程：
1. `netmind_auth_client.verify_token()` → 失败 401（token 无效）/ 502（NetMind 不可达，前端提示稍后重试——不把上游故障伪装成密码错误）。
2. `UserRepository.upsert_netmind_user(user_system_code, email, nickname)`：
   - 按 `user_id = user_system_code` 查；存在 → 更新 email/display_name（NetMind 侧可能改）+ `update_last_login`；
   - 不存在 → INSERT（`role='user'`, `user_type='individual'`, `status='active'`，`display_name=nickname`，`email=email`）。
   - email 唯一性：users.email 无唯一约束（保持不动，铁律 6）；userSystemCode 本身唯一即可，email 仅展示/迁移用。
3. 首次创建 → `quota_service.init_for_user` 直接种免费额度（失败不阻断登录，与现注册逻辑一致）。
4. `schedule_user_no_quota_rearm`（保留现登录后的 job 复活逻辑）。
5. 签自家 JWT 返回。

**删除（cloud 语义）**：
- `POST /api/auth/login` 的 cloud 密码分支（local 分支保留原样）
- `POST /api/auth/register` 整个端点
- `POST /api/auth/create-user` 改为 **local 模式专用**（cloud 下 404/403）——顺手消灭无凭证开放建号漏洞
- `POST /api/invite/internal/issue`、`/api/admin/invite/*`（整个邀请码路由面）**直接删除**（铁律 2）；invite_codes 表数据保留不动（铁律 6）。连带：narranexus-website 的「申请邀请码」页面失去后端，需要联动改造为直接引导注册（website 源码在 EC2 本地，不在本仓库——列入上线 checklist）

**身份取用顺手收口**（铁律 8）：`timezone` / `onboarding` / `create_agent` 等仍信任 body user_id 的旧端点，统一改为 `resolve_current_user_id(request)`。这是登录改造的直接相邻面，本次一起修。

### 3.3 users 表语义（无 DDL 变更）

| 列 | 旧语义 | 新语义 |
|---|---|---|
| `user_id` | 自造用户名（登录名） | **NetMind userSystemCode**（32-hex，VARCHAR(64) 容量足够） |
| `password_hash` | bcrypt | cloud 不再写入；列保留（local 不用它，留着不动） |
| `email` | 未使用 | NetMind 账号 email（每次登录刷新） |
| `display_name` | 可空 | NetMind nickname（每次登录刷新；用户在我们侧改名后续再说） |
| `metadata` | onboarding 等 | 增加 `netmind` 命名空间（迁移标记、Phase 2 的 provisioning 标记） |

workspace 目录 `{agent_id}_{user_id}` 拼接逻辑不改——新用户的目录名自然变成 `{agent_id}_{userSystemCode}`。

### 3.4 额度发放（2026-06-10 拍板：取消邀请码，注册即送）

- netmind-login 首次创建用户时直接 `quota_service.init_for_user`（额度值沿用 `SYSTEM_DEFAULT_QUOTA_INPUT_TOKENS/_OUTPUT_TOKENS` env）。
- 不做任何准入闸门、不做 redeem 端点、不做策略开关——免费层的滥用兜底就是 token 上限本身 + NetMind 注册侧的人机验证/一次性邮箱拦截。
- 邀请码体系整体下线：`backend/routes/invite.py`、`admin_invite.py` 删除；`INTERNAL_INVITE_SECRET` env 移除；invite_codes 表保留数据（迁移脚本还要用它的 email 关联）。
- 历史方案（QUOTA_GRANT_POLICY 双模式 + Settings 填码）已作废，记录在 research/00 决策 D7。

### 3.5 存量用户迁移（一次性，离线脚本）

**前置（外部，Bin 哥对接 NetMind）**：取得 email→NetMind 账号的代建通道。两种形态任一：
- (a) NetMind 给我们一个受信代注册接口（`/xyz/insertUserInfo` 同款，type=2 email 通道）——脚本可全自动；
- (b) NetMind 运营批量建号后给我们 `email → userSystemCode` 映射表——脚本吃 CSV。

**脚本**：`scripts/migrate_users_to_netmind.py`（NarraNexus 仓库，独立可执行，绝不进 lifespan）：

1. **盘点**：`SELECT users LEFT JOIN invite_codes ON used_by_user_id` 取每个存量用户的 email；email 缺失/重复的输出清单（`--report` 模式，先跑给人看）。
2. **建号/取映射**：按 (a) 调接口或 (b) 读 CSV，得到 `old_user_id → new_user_system_code`。
3. **迁移**（`--execute`，要求 stack 已停）：
   - 单事务内 UPDATE 16+ 张表的身份列（清单以调研报告 01 §3.1 为准：agents.created_by、events.user_id、mcp_urls、inbox_table、module_instances、instance_jobs、user_providers(user_id+owner_user_id)、user_slots、user_quotas、user_notifications、bus_agent_registry.owner_user_id、teams.owner_user_id、bundle_preflight_sessions、skill_archives、instance_artifacts、invite_codes.used_by_user_id、users 本行）；
   - workspace 目录改名 `{agent_id}_{old}` → `{agent_id}_{new}`（文件系统操作在 DB 事务提交后做，失败可重跑——目录改名幂等）；
   - users.metadata 写迁移标记（审计）。
4. **验证**：`--verify` 模式逐表 count 旧 user_id 残留 = 0。

**运维流程**：dev EC2 全流程演练 → prod 停服窗口执行（`make app-down` → 脚本 → `make app-up`）。迁移期间用户密码由 NetMind 侧流程解决（找回密码/首次设置），不在我们脚本范围。

#### 3.5.1 手工 one-to-one 迁移变体（2026-06-11 新增设计，暂不实现）

**背景**：自动盘点（invite_codes 关联 email）会漏两类账号——(i) 没有可解析 email 的旧账号；(ii) 关联到的 email 不对、需要人工指定/换优先的账号。这些必须由运营**手工**给出 `旧账号 id → 该用户在 Power 的真实 email` 的对应关系。

**输入形态**：CSV `old_user_id, power_email`（运营手工填，一行一个）。这与现有 `--execute` 吃的 `old_user_id, new_user_system_code` 不同——这里运营知道的是 email，不是 userSystemCode。

**设计（在现有脚本上加一个 resolve 阶段，不改 execute/verify 内核）**：
1. `--resolve --pairs manual.csv`：对每个 `(old_user_id, power_email)`，把 email 换成 userSystemCode，产出标准的 `old_user_id, new_user_system_code` 映射 CSV（喂给现有 `--execute`）。email→userSystemCode 的换取依赖 §3.5 前置 (a) 代注册接口的「email 已存在则返回既有 code」语义，或 (b) 运营回填。
2. 手工映射与自动盘点**合流去重**：同一 old_user_id 以手工 CSV 为准（手工优先级高于自动盘点——这就是「换优先」）。
3. 其余（单事务改表、目录改名、verify）完全复用现有内核，零改动。

**未实现原因**：email→userSystemCode 的换取通道（§3.5 前置）尚未从 NetMind 拿到；通道到位后这个 resolve 阶段是小增量。现有 `migrate_users_to_netmind.py`（T6 已实现）的 execute/verify 内核已就绪，手工变体只是前面多一个 resolve 步骤。

**没有 email 的账号**：`--report` 已会把它们列为 `no_email`；运营要么补 email 进手工 CSV，要么决定弃用该账号（数据保留、不迁移、无法再登录）。这是产品决策不是脚本能力。

### 3.6 对 local/桌面模式的影响分析（铁律 7）

**结论：local 模式认证行为零变更。** 逐项核对：

| 项 | local 现状 | 本次影响 |
|---|---|---|
| 中间件 local 分支（X-User-Id，auth.py:394-433） | 不验签、header 即身份 | **不动**（netmind-login 是新增路由，中间件无改动） |
| `POST /api/auth/login` | local 分支：user_id 存在即登录 | **保留**；只删 cloud 密码分支——同一函数动刀，回归必测 |
| `POST /api/auth/create-user` | local 建号入口（CreateUserDialog） | **保留**；仅加 cloud 下 404 守卫 |
| sqlite / users 表 | user_type='local'，自造用户名 | 无 DDL、无数据变更；cloud 用户（32 hex）与 local 用户共存不冲突 |
| 配额闸门 | local 永远短路（quota /me 返回 enabled:false） | 额度种子只发生在 cloud upsert 路径 |
| 前端 LoginPage local 形态 / ModeSelect / SetupPage | 仅 user_id 登录 | LoginPage 重构时 local 形态保留——共享组件动刀，回归必测 |
| Sidebar 显示 | 显示 userId（可读名） | 改为 `displayName ?? userId`，local 自然回落（CreateUserDialog 本就有 displayName 字段） |
| netmind_auth_client / 迁移脚本 | — | 只被 cloud 链路调用；迁移脚本只对 MySQL 跑 |
| 桌面端 dmg | 与 run.sh 同一套 local 链路 | 同上；dmg 冒烟列入测试策略 |

**两个需要留意的共享面**（不是 local 行为变更，是误伤风险点）：
1. `/api/auth/login` 和 `LoginPage.tsx` 是 cloud/local 双形态共享文件，删 cloud 分支时必须保住 local 分支——§7 回归项。
2. timezone/onboarding/create_agent 身份收口到 `resolve_current_user_id` 后，**不带 X-User-Id header、只在 body 传 user_id 的调用方会 401**。前端 api.ts 统一注入双 header 不受影响；实现期 grep 仓库内 CLI/脚本类直调这些端点的地方（如 setup 脚本），有则同步改。

### 3.7 测试通道（smoke / e2e 的 make_user）

抄 Arena 的双开关 dev-bypass（netmind.ts:8-15）：

- `NETMIND_DEV_BYPASS=1` 且 deployment 非 prod 域名时，`netmind-login` 接受 `dev-bypass-<email>` 形态的 token：跳过 NetMind 调用，user_system_code = `devbp_` + sha1(email) 前 24 位。
- 双保险：env 开关 + token 前缀，缺一不可；prod `.env` 永不设此开关。
- smoke 的 `make_user()` fixture 改为调 `netmind-login` + bypass token；L5 多用户隔离套件同步改。
- 真实链路测试用 protago-dev test 环境账号（D8 对接项）。

---

## 4. 前端设计

### 4.1 移植 Arena Login 套件

从 Arena `frontend/src/components/Login/` 移植（改为我们的 UI 风格与 frontend 规范）：

| Arena 源 | 我们的落位 | 改动 |
|---|---|---|
| `auth-constants.ts` | `frontend/src/lib/netmindAuth/constants.ts` | URL 从 build env（`VITE_NETMIND_AUTH_API` 等）读取，prod/test 切换跟部署走而不是硬编码域名判断 |
| `auth-request.ts`（axios + token header + form-urlencoded） | `netmindAuth/request.ts` | 改为项目统一的 fetch 封装风格 |
| `utils/crypto.ts`（DES-CBC 密码加密） | `netmindAuth/crypto.ts` | 原样（NetMind 协议要求） |
| `LoginCard.tsx` + `useThirdPartyAuth.ts` + `AuthBindDialog` | `components/auth/NetmindLoginCard.tsx` 等 | UI 重做，逻辑保留（emailLogin / OAuth 弹窗 postMessage / 三种 bandType 绑定流） |

`sysCode` 用 `f925fc2c`（复用 power，与 Arena 一致，D8）。

### 4.2 登录/注册页

- `LoginPage` cloud 形态替换为 NetmindLoginCard（邮箱密码 + Google/Microsoft/GitHub）；local 形态不动。
- **注册**：v1 不内嵌 NetMind 注册表单（要 reCAPTCHA Enterprise + 邮箱验证码两步，内嵌成本高且 Arena 也没做）——「Sign up」链接跳 NetMind 注册页，注册完回来登录。`RegisterPage` 删除。
  - 若后续要内嵌：NetMind `/register/registerUser` + `/register/sendCode` API 都在，留作增强。
- 登录成功：`POST /api/auth/netmind-login` → configStore 存 `{userId, token, role}` + 新增 `netmindToken`（Phase 2/3 用）+ 新增 `displayName/email`。
- `?next=` 回跳、`ProtectedRoute` 探活逻辑不变。

### 4.4 Power 登录态打通（免登，2026-06-11 Power PM 反馈纳入）

> PM 反馈：用 power 的 sysCode 可以把 power 登录态与 web 版 NarraNexus 打通——在 power（netmind.ai）登录过就不用在 NarraNexus 重新登录。

**原理**：sysCode 决定 JWT 用哪套签名密钥（power/Xyz/life 三套，`InitConst.java:60-63`）。我们复用 power 的 `f925fc2c`，则用户在 power 登录拿到的 loginToken 与在我们登录组件里拿到的是**同一种 token**——`/user/balance` 同样接受。打通 = 想办法把用户已有的 token 递到我们手里：

1. **`?token=` URL 直通（确定做，机制已验证）**：NarraNexus 前端 App 初始化时检测 `?token=` query → 立即从 URL/history 抹除 → 调 `netmind-login` 换发自家 JWT。参考实现 Arena `UserContext.tsx:69-100`（入站）/`MePage.tsx:586`（出站）。netmind.ai 侧任何入口链接带上 `?token=` 即可免登进入 NarraNexus（power 站内入口的添加需 Power 前端配合，见 §10）。
   - 注意：此项原属 Phase 2（Arena 入口），现升格为 Phase 1 通用基础设施；Phase 2 只是在此之上加 `source=arena` 参数。
2. **accounts 域静默取 token（待 Power 确认是否有现成机制）**：用户直接打开 agent.narra.nexus（不经链接跳转）时，若 accounts.netmind.ai 域内已有登录态，理论上可通过隐藏 iframe/弹窗 + postMessage 静默取回 token（accounts 前端代码不在我们可见仓库内，**是否支持此模式需 Power 团队确认**）。有则体验更顺；没有则用户点一次「用 NetMind 登录」也只是确认动作（已登录态下 OAuth 弹窗/表单可能直接通过）。

反向（NarraNexus → netmind.ai 免登）同理可用 `?token=` 出站，Phase 3 充值跳转会用到。

### 4.5 user_id 不再可读的展示适配

user_id 从可读用户名变为 32 位 hex，**所有把 user_id 显示给人看的地方改为 display_name（兜底 email 前缀）**：
- `components/layout/Sidebar.tsx:196`（用户名文本）、`:209`（RingAvatar 的 label/title）——已核实是当前唯一直接显示 userId 的 UI；
- netmind-login 响应增加 `display_name`/`email` 字段，configStore 同步持有；
- local 模式用户名仍可读，沿用现状（display 取值统一走 `displayName ?? userId`，local 自然回落）。
- 实现期全局 grep `{userId}` 再核一遍，防新增显示点漏网（铁律 8）。

### 4.3 token 生命周期

- 自家 JWT 过期（7 天）→ 现有 401 → `narranexus:auth-expired` → 跳登录页，机制不变。
- 留存的 netmindToken 过期（15 天/被轮换）不影响会话——只在 Phase 2/3 的显式动作时发现失效，届时引导重新登录一次刷新。
- 登出：清 configStore（含 netmindToken）；是否同时调 NetMind `/user/logout` ——**不调**（用户可能还在 Arena/netmind.ai 用同一账号，我们登出不该把人家全家登出）。

---

## 5. 部署侧变更（仓库 B）

`stacks/narranexus-app/.env.example`：
- 新增：`NETMIND_AUTH_API_URL`、`NETMIND_AUTH_TIMEOUT_SECONDS`、（测试环境模板加 `NETMIND_DEV_BYPASS`）
- 删除：`INVITE_CODE`（早已废弃的遗留 env）、`INTERNAL_INVITE_SECRET`（邀请码体系下线）
- 保留：`JWT_SECRET`（继续签自家 token）、`SYSTEM_DEFAULT_*` 全套（注册即送额度的额度值来源）
- 前端构建 env：`VITE_NETMIND_AUTH_API` / `VITE_NETMIND_ACCOUNTS_URL` / `VITE_NETMIND_SYS_CODE`（进 Dockerfile.frontend build args）

Caddy/nginx 零改动（登录调用是前端直连 NetMind 域名 + 自家 `/api/`，无新回调路径；OAuth 弹窗页在 accounts.netmind.ai 域内闭环，postMessage 回传不经过服务器）。

---

## 6. 安全清单

| 项 | 处置 |
|---|---|
| 开放 create-user 端点（现存漏洞） | cloud 下移除（3.2） |
| NetMind token 服务端持久化 | 不持久化；verify 响应中的 loginToken 等字段不落日志 |
| dev-bypass | 双开关 + prod 永不开启；code review 检查点 |
| 登录速率限制 | NetMind 侧已有（错误次数冻结 + reCAPTCHA）；我们的 netmind-login 加 `_rate_limiter`（现成模块）防 verify 转发被滥用 |
| JWT_SECRET | 不变；上线时按 SECURITY_REMEDIATION 计划轮换一次（顺手使所有旧自建会话失效，干净切换） |
| 旧端点残留 | register/login(cloud)/create-user(cloud) 直接删代码而非留 410（铁律 2） |

---

## 7. 测试策略

- **单测**：netmind_auth_client（mock HTTP：成功/401/超时/字段缺失）、upsert（新建/更新/email 变更）、首次创建种额度（成功/quota 失败不阻断登录）。
- **集成**：netmind-login 全流程（dev-bypass 通道）；middleware 对新端点豁免；额度耗尽后 402 闸门行为不回归。
- **真实链路**：protago-dev test 环境真账号跑通 emailLogin + OAuth（手动验收项）。
- **迁移脚本**：dev EC2 上对 dev RDS 全流程演练（report → execute → verify），抽查 agent 可正常打开、workspace 路径正确。
- **回归**：local 模式登录/建号/X-User-Id 全链路必须无感（铁律 7）；桌面端冒烟。
- 既有测试适配：`tests/utils/test_deployment_mode.py` 等涉及 auth 行为的用例同步更新。

---

## 8. 涉及面与 mirror md（铁律 10）

| 仓库 A 文件（预计 touched） | 动作 |
|---|---|
| `backend/auth.py` | 改（删 cloud 密码工具的引用面、保留 JWT/中间件） |
| `backend/routes/auth.py` | 大改（新增 netmind-login、删 login-cloud/register、create-user 限 local、身份收口） |
| `backend/routes/invite.py`、`backend/routes/admin_invite.py` | 删除（+删 mirror md）；main.py 路由注册同步移除 |
| `src/xyz_agent_context/services/netmind_auth_client.py` | 新增（+新增 mirror md） |
| `src/xyz_agent_context/repository/user_repository.py` | 增 upsert_netmind_user |
| `scripts/migrate_users_to_netmind.py` | 新增（+mirror md） |
| `frontend/src/lib/netmindAuth/*`、`components/auth/*` | 新增（+mirror md） |
| `frontend/src/pages/LoginPage.tsx` | 大改；`RegisterPage.tsx` 删除（删 mirror md） |
| `frontend/src/stores/configStore.ts` | 增 netmindToken |
| `frontend/src/components/settings/`（redeem 卡片） | 增 |

每个文件改前先读对应 `.mindflow/mirror/.../X.md`，行为变更同 commit 更新。

---

## 9. 风险与依赖

| 风险/依赖 | 等级 | 缓解 |
|---|---|---|
| NetMind 代建账号通道（迁移前置） | **外部阻塞（迁移子任务）** | Bin 哥对接；新用户登录功能不被它阻塞，可先上线、迁移随后 |
| protago-dev test 环境访问 | 外部 | Bin 哥对接；期间开发用 dev-bypass + mock 推进 |
| `/user/balance` 接口契约变更（无正式 API 文档） | 中 | client 层防御性解析 + 失败 502 语义；与 NetMind 团队确认接口稳定性 |
| sysCode 复用 f925fc2c 的合规性 | 低 | 与 Arena 同款；如 NetMind 要求独立 code，改一处常量 |
| 迁移脚本对 16+ 表的覆盖完整性 | 中 | 以调研清单为基线 + 脚本内 grep schema_registry 双重核对 + verify 模式 |
| NetMind auth 服务故障期间无法登录 | 接受 | 已登录用户不受影响（自家 JWT 独立）；这正是 D1 换发方案的价值 |

---

## 10. 向 NetMind 后端工程师索要/确认的清单

### 10.0 已实测确认（2026-06-11，dev 环境真账号）

用 Power 提供的 dev 测试账号，经一次性 GitHub Actions runner（工作站无法直连 AWS）实跑了 `emailLogin` + `/user/balance` 全链路，确认：

- **`emailLogin` 200 成功**：Arena 的密码协议（DES-CBC，key=iv=signStr，PKCS7，hex）+ `ckType=2` 对我们完全可用，**reCAPTCHA 确实不触发**（与 Power PM 2026-06-11 反馈一致）。
- **userSystemCode 字段名 = `userSystemCode`**（驼峰，32-hex），同值也出现在 JWT 的 `aud` claim。我们 client 的假设正确，无需改代码。
- **`/user/balance` 200，结构 = `{data: {user: {...}, userAccount, isTest}}`**，与 client 的 `body["data"]["user"]` 取法吻合。
- **响应 user 对象确实携带 `loginToken/nettyToken/accessToken/salt/loginPassword/googleKey` 等敏感字段**——印证 client 里「存 raw 前剥离 token 类字段 + 不打日志」的防护是必要的，非多余。
- 顺带确认了 `nickName`、`email` 字段存在（display 适配有数据来源）。

→ 本节使下面 10.1 表 #1（字段名）、#2（ckType=2）、10.2 #2（reCAPTCHA）从「待确认」变为「已确认」。

### 10.1 现成接口——只需「授权使用 + 契约确认」（Arena 已在用，不需要新开发）

| # | 接口/资源 | 我们的用途 | 需要他们确认的点 |
|---|---|---|---|
| 1 | `POST /user/balance` | 后端验证 token + 取用户身份 | ✅ 字段名 `userSystemCode` 已实测确认（10.0）；仍需：接口稳定性承诺（无正式文档，属「借用」）+ 服务端调用频率限制 |
| 2 | `POST /user/emailLogin` | 前端邮箱密码登录 | ✅ 已实测：`ckType=2` 不触发人机验证、DES 协议可用（10.0）。无待确认项 |
| 3 | `GET /user/loginMsg/{type}` + `POST /user/userCallBack` + `accounts.netmind.ai/auth.html` | Google/Microsoft/GitHub OAuth 弹窗流 | auth.html 的 postMessage 是否校验 opener origin（是→需把我们域名加白） |
| 4 | `GET /user/logout`、`POST /user/refreshAT` | 备用（v1 我们不调 logout） | 无 |
| 5 | sysCode | 登录请求必带 | **已定：复用 power 的 `f925fc2c`**（2026-06-11 Power PM 反馈：用 power sysCode 可打通 power 登录态与 NarraNexus——同 sysCode = 同签名密钥 = token 通用；单独 sysCode 反而隔断打通） |
| 7 | Power 登录态免登打通 | `?token=` 直通 + （可选）accounts 静默授权 | 见 §4.4：需 Power 前端在 netmind.ai 站内合适位置加带 `?token=` 的 NarraNexus 入口；并确认 accounts.netmind.ai 是否支持「已登录态静默返回 token」的页面模式 |
| 6 | test 环境 | 开发与验收 | `userauth.protago-dev.com` / `accounts.protago-dev.com` 对我们开放 + 几个测试账号 |

### 10.2 需要他们配置/新提供的（真正的「找人办事」清单）

| # | 事项 | 性质 | 说明 |
|---|---|---|---|
| 1 | **CORS 白名单加我们的域名** | 配置 | 前端从 `agent.narra.nexus`（+ dev 域名）直连 `auth-api.netmind.ai`——Arena 的 `arena42.ai` 能调说明 CORS 机制存在，确认是通配还是白名单；白名单则要加我们 |
| 2 | ~~reCAPTCHA key 域名授权~~ | 已消除 | ✅ Power PM 确认 + 实测：`ckType=2` 路径完全不触发 reCAPTCHA，无需任何域名授权。本条作废 |
| 3 | **存量用户代建账号通道** | 新开通/复用 | `/xyz/insertUserInfo` 同款受信代注册（MD5 共享密钥，email 通道）：给我们一个接入 code + push key；**必须明确「email 已是 NetMind 用户」时返回既有 userSystemCode 而非报错**（存量用户可能已有 NetMind 账号）。替代方案：他们运营批量建号，回给我们 email→userSystemCode CSV |
| 4 | 代建账号的密码设置路径 | 确认 | 代建的账号无用户已知密码——确认「忘记密码/重置」流程对代建账号可用，或代建时触发设置密码邮件 |
| 5 | （可选，不阻塞）轻量 token 校验接口 | 新开发 | `/user/balance` 是借用且响应含 loginToken 等敏感字段；若他们愿意提供只返回 `{userSystemCode, email, nickname}` 的 introspection 接口更干净。不给也能跑 |

### 10.3 顺带预约（Phase 2/3 用，可同一次沟通提出）

- Arena 团队：入口链接 PR 协调（Phase 2，D9）；「受信平台代绑定 ownerEmail」增强（不阻塞）。
- Power/billing 团队：`POWER_API_KEY`（billing admin key）、`/inference/addApiToken` 普通用户权限确认（Phase 3）。

### 10.4 可直接转发给 NetMind 后端工程师的沟通清单

> 以下为给对方的原文，可直接复制（按需删减）。

---

Hi，我们是 NarraNexus 团队。我们准备把 NarraNexus 云端版的用户注册/登录接到 NetMind 账号体系，集成方式整体照搬 Arena 的模式（前端直调 auth-api 登录，后端用 `/user/balance` 验 token）。需要麻烦你们这边几件事：

**一、配置类（阻塞我们开发联调）**

1. **CORS**：我们前端会从 `agent.narra.nexus` 和 `dev-agent.narra.nexus` 直连 `auth-api.netmind.ai`（test 环境连 `userauth.protago-dev.com`）。请确认 auth-api 的 CORS 是白名单制还是通配；白名单的话请把上面几个域名加进去（prod + test 都要）。
2. **sysCode**：我们复用 power 的 `f925fc2c`（与 Arena 相同，PM 已确认这样能打通 power 登录态）。如你们有异议请提出。
3. **test 环境**：感谢已给的 dev 测试账号，我们已用它实测跑通 `emailLogin` + `/user/balance`。请确认 `userauth.protago-dev.com` / `accounts.protago-dev.com` 可继续给我们用于联调。

（reCAPTCHA 一项已无需处理——PM 确认且我们实测 `ckType=2` 路径不触发人机验证。）

**二、接口契约确认（不用开发，确认即可）**

4. `POST /user/balance`：我们后端拿用户 loginToken 调它验证 token（header `token: Bearer <jwt>`），从响应 `data.user` 取身份。**字段名 `userSystemCode` 我们已实测确认**。仅需再确认两点：
   - 这个接口短期内没有 breaking change 计划（Arena 也在用）；
   - 我们服务端调用有没有频率限制需要注意。

**三、存量用户迁移（需要你们提供一个通道，二选一）**

我们现有一批内测用户（每人有 email），想帮他们转成 NetMind 账号：

6. **方案 A（优先）**：给我们开一个受信代注册通道——类似你们给 XYZ 的 `/xyz/insertUserInfo`（共享密钥签名，email 通道），我们服务端按 email 批量建号。**关键要求：如果某个 email 已经是 NetMind 用户，请返回该用户的既有 userSystemCode，不要报错**。
7. **方案 B（兜底）**：我们给你们 email 列表，你们运营批量建号后回给我们 `email → userSystemCode` 的映射表。
8. 不管哪个方案：代建的账号用户自己不知道密码，请确认「忘记密码/重置密码」流程对代建账号可用（或者建号时能触发一封设置密码邮件）。

**四、可选（有更好，没有不阻塞）**

9. 一个轻量的 token 校验接口：只返回 `{userSystemCode, email, nickname}`。现在 `/user/balance` 的响应里带 loginToken 等敏感字段，作为校验接口用偏重了。你们方便就提供，不方便我们继续用 `/user/balance`。

后续我们还会有两件事找对应团队：Arena 页面加一个 NarraNexus 入口链接（我们提 PR）；billing 侧申请一把 admin key 做 credits 互通（晚些时候）。先谢谢！

---

## 11. 其他开放问题（实现期敲定）

1. 存量用户中 email 缺失者的处理（等迁移脚本 `--report` 盘点结果，可能为零）。
2. staff 角色的指定方式（现状 DB 手改 role；迁移脚本保留 role 列值即可，无新设计）。
