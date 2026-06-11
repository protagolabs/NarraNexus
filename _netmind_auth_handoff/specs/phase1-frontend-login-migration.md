# Phase 1 前端设计：登录迁移到 NetMind 账号体系

> 日期：2026-06-11
> 状态：设计稿（待 Bin 哥 review spec 后进 plan/实现）
> 前置：后端 Phase 1 已完成（`phase1-user-system-unification.md`），分支 `feat/netmind-auth`（基于 origin/dev，已 merge 后端 7 commit）
> 实现仓库：NarraNexus（前端）+ NarraNexus-deploy（仅 `.env.example` + 前端运行期注入脚本）

---

## 1. 目标与范围

把 NarraNexus **cloud 模式**的登录从自建（user_id + 密码）改为 NetMind 账号登录，对接后端已就绪的 `POST /api/auth/netmind-login`。

**本次做**：
- 内嵌 NetMind 邮箱密码登录（前端直调 NetMind `emailLogin`，DES 加密）
- 第三方 OAuth 登录：Google / Microsoft / GitHub（弹窗 + postMessage + bandType 绑定流）
- `?token=` 入站直通（场景 A：Power 点链接进来免登；复用同一后端入口）
- 双 token 管理（自家 JWT + 留存 NetMind loginToken 供 Phase 2/3）
- Sign up 改为跳 NetMind 注册页外链；删除 RegisterPage

**本次不做**（明确排除）：
- 场景 B 冷启动桥接（依赖 NetMind 提供重定向桥接端点，外部阻塞——见 research/00 §四 D 的研究结论）
- 真实端到端验收（工作站到 AWS NetMind 网络不通；本地用 dev-bypass 自测，真实登录验收留到部署 dev EC2，见 §8）

**不变量**：
- local / 桌面模式登录完全不动（user_id-only + CreateUserDialog）
- 自家 JWT 会话机制不变（中间件、22 路由、WS 认证零改动——后端已保证）

---

## 2. 架构

```
┌─ 浏览器（cloud 模式）──────────────────────────────────────────┐
│ LoginPage (cloud 分支)                                          │
│  ├─ 邮箱密码 → useNetmindAuth.emailLogin()                      │
│  │     DES 加密 → POST {NETMIND_AUTH_API}/user/emailLogin       │
│  │     (header token: Bearer; ckType=2; sysCode f925fc2c)       │
│  ├─ OAuth 按钮 → window.open(accounts.netmind.ai/auth.html)     │
│  │     popup postMessage{code,state} → /user/userCallBack       │
│  │     首次第三方账号 → AuthBindDialog (bandType 1/2/3)          │
│  └─ 任一路径拿到 NetMind loginToken                              │
│        ↓                                                        │
│  api.netmindLogin(loginToken, source?)                          │
│     POST /api/auth/netmind-login                                │
│        ↓ 后端验证+换发自家 JWT                                   │
│  configStore.login(userId, ourJwt, role, {displayName, email})  │
│  configStore.setNetmindToken(loginToken)   ← Phase 2/3 用       │
│        ↓                                                        │
│  getAgents → 跳转 (next / RootRedirect)                         │
└────────────────────────────────────────────────────────────────┘

App 初始化（早于路由）: 检测 ?token= → 即取即删 → api.netmindLogin → 登录态
```

**两段碰 NetMind 的调用**：(a) 浏览器直连 `emailLogin`/`userCallBack`（需 NetMind 给我们域名开 CORS）；(b) 后端 `/user/balance` 验 token（已实现）。

---

## 3. 新增前端模块（移植 Arena，适配 dev 栈）

新建 `frontend/src/lib/netmindAuth/`。Arena 用 axios + 自有组件 + Zustand auth-store；我们改造成 dev 的 fetch 风格 + `@/components/nm` 原语 + 现有 configStore。

| 文件 | 来源 | 改造要点 |
|---|---|---|
| `constants.ts` | Arena `auth-constants.ts` | URL / sysCode 从**运行期注入**读（见 §6），不硬编码域名；公共参数 `{deviceId, clientType:5, clientVersion, sysCode}` |
| `crypto.ts` | Arena `utils/crypto.ts` | DES-CBC（key=iv=signStr，PKCS7，hex）原样。**新增依赖 `crypto-js`**（Web Crypto 不支持 DES；Arena 同款，已决定采用） |
| `request.ts` | Arena `auth-request.ts` | 改 fetch；`token: Bearer` header 约定保留；form-urlencoded body |
| `useNetmindAuth.ts` | Arena `useThirdPartyAuth.ts` + auth-store.login | emailLogin + OAuth 弹窗 postMessage + bandType 绑定流；**删 ReCaptcha**（实测 ckType=2 不触发）；成功回调汇入 `api.netmindLogin` |
| `types.ts` | Arena `types/auth.ts` 子集 | NetmindUser / AuthBindInfo |

**模块边界**：netmindAuth 自成一体，只暴露 `useNetmindAuth()` hook（emailLogin / oauth / bind 三个动作 + loading/error/bindDialog 状态）给 LoginPage。LoginPage 不直接碰 NetMind URL 或 DES。

---

## 4. 改动现有文件

### 4.1 configStore.ts
- 新增字段：`netmindToken: string`、`displayName: string`、`email: string`
- `login()` 签名扩展：`login(userId, token?, role?, profile?: {displayName, email})`
- 新增 `setNetmindToken(token)`；`logout()` 清掉三个新字段
- persist 不变（同一 `narra-nexus-config` key）

### 4.2 lib/api.ts
- 新增 `netmindLogin(netmindToken, source?): Promise<NetmindLoginResponse>` → `POST /api/auth/netmind-login`
- 现有 `login()` 保留（local 模式仍用）；token 注入拦截器不变（自家 JWT 走 Authorization header）

### 4.3 pages/LoginPage.tsx
- **cloud 分支**：替换为 NetMind 登录卡——邮箱 + 密码框 + 「Sign In」+ 分隔线 + 3 个 OAuth 按钮 + AuthBindDialog（条件渲染）。沿用 `@/components/nm` 原语与现有视觉（logo、Chip、卡片布局保留）
- **Sign up**：改为 `<a href={NETMIND_REGISTER_URL} target="_blank">` 外链（不再 `navigate('/register')`）
- **local 分支**：原样保留（user_id + CreateUserDialog）
- `?next=` 回跳、isSafeReturnTo 不变

### 4.4 App.tsx
- App 初始化 useEffect 加 `?token=` 接收（抄 Arena UserContext.tsx:69-100）：读 token → `history.replaceState` 即取即删 → 已登录则跳过 → `api.netmindLogin(token, source)` → 成功进登录态。`source` 从 query 读，存 sessionStorage（Phase 2 用，本次只透传）
- 删 `/register` 路由 + `RegisterPage` lazy import

### 4.5 删除
- `pages/RegisterPage.tsx`（+ mirror md `.mindflow/mirror/frontend/src/pages/RegisterPage.tsx.md`）

---

## 5. 数据流细节

**邮箱密码**：输入 → `signStr=随机8位` → `DES(password, signStr)` → `POST emailLogin` → `{data:{loginToken, user}}` → `api.netmindLogin(loginToken)` → 后端换 JWT → configStore 写入 → getAgents → 跳转。

**OAuth**：点按钮 → `window.open(accounts.netmind.ai/auth.html?authApi=.../user/loginMsg/{TYPE})` → 用户在 NetMind/第三方域授权 → 弹窗 `postMessage({type:'auth',code,state})` → 父窗口 `userCallBack` → 若返回 loginToken 直接登录；若返回 bandType（1 需邮箱验证码 / 2 确认第三方邮箱 / 3 绑定已有）→ AuthBindDialog → 再次 userCallBack → 汇入同一 `api.netmindLogin`。

**关键**：所有路径最终都收敛到 `api.netmindLogin(loginToken)` 这一个出口——后端只认 loginToken，不关心它从密码还是 OAuth 来。

---

## 6. 配置注入（dev/prod 端点不同走运行期，不烤进 bundle）

NetMind URL 在 dev / prod 不同，且我们是「一个镜像多环境」。因此**不用 build 期 `VITE_*`**（会把 dev URL 烤死在 bundle），而是扩展现有运行期注入 `window.__NARRANEXUS_CONFIG__`（部署时 `entrypoint-frontend.sh` 写 config.js，与现有 `mode`/`apiUrl` 同机制）。

新增运行期配置键（`lib/runtimeConfig.ts` 的 shape 扩展）：
| 键 | dev 值 | prod 值 |
|---|---|---|
| `netmindAuthApi` | `https://userauth.protago-dev.com` | `https://auth-api.netmind.ai` |
| `netmindAccountsUrl` | `https://accounts.protago-dev.com` | `https://accounts.netmind.ai` |
| `netmindSysCode` | `f925fc2c` | `f925fc2c` |
| `netmindRegisterUrl` | （NetMind 注册页，待 Power 给规范 URL） | 同 |

**连带改部署仓库**（仓库 B）：`docker/entrypoint-frontend.sh` 把这 4 个键也写进 config.js；`.env.example` 增对应 env。本 spec 的部署侧改动仅此。dev 本地开发用 `.env.local` 的 `VITE_*` 兜底（仅本地，不进镜像）。

---

## 7. 测试

vitest（`pnpm test`），mock fetch，不碰真网络：
- `crypto.ts`：DES 加密输出对已知 (message, key) 的固定密文（与 Arena 对齐的金标准向量）
- `useNetmindAuth`：emailLogin 成功 / 失败、OAuth callback 直登 / 返回 bandType 走绑定、bind 提交成功 / 失败（全 mock fetch）
- `configStore`：新字段写入 / logout 清除 / 切账号重置
- App `?token=`：取-删-登录、已登录跳过、无 token 不触发
- LoginPage：cloud 渲染 NetMind 卡、local 渲染原表单、Sign up 外链

**本地整合自测（dev-bypass）**：见 §8。

---

## 8. 验证策略（受网络墙约束，诚实记录）

工作站到 AWS NetMind 端点网络不通（`userauth.protago-dev.com` / `auth-api.netmind.ai` 均超时，github 正常）。因此：

| 验证项 | 能否本地做 | 方式 |
|---|---|---|
| 单元测试（逻辑正确性） | ✅ | vitest + mock fetch |
| 我们这侧全栈整合（?token= → netmind-login → JWT → 登录态） | ✅ | 后端 `NETMIND_DEV_BYPASS=1`，前端喂 `dev-bypass-<email>` token 走 netmind-login，跳过真实 NetMind |
| typecheck / build | ✅ | `tsc` / `pnpm build` |
| 真实邮箱密码 / OAuth 往返（浏览器→真 NetMind） | ❌ 本地 | 浏览器也在墙内；留到**部署 dev EC2** 或一次性 runner 模拟浏览器流程验收 |

**结论**：代码可在本地写完并自测绿（单测 + dev-bypass 整合 + build），但「真账号点着登录成功」的验收是部署后的单独一步，不阻塞编码。NetMind 接口契约已实测确认（字段名 / 协议 / 返回结构），非盲写。

### 8.1 本地 dev-bypass 烟测步骤（不碰真实 NetMind）

验证「我们这侧」全栈打通，无需 NetMind 可达：
1. 后端起在 cloud 语义 + dev-bypass：
   ```bash
   NETMIND_DEV_BYPASS=1 \
   DATABASE_URL=sqlite:///tmp/nx_smoke.db \
   NARRANEXUS_DEPLOYMENT_MODE=cloud \
   JWT_SECRET=dev-smoke-secret \
   SYSTEM_DEFAULT_LLM_ENABLED=false \
   uv run uvicorn backend.main:app --port 8000
   ```
   （cloud 语义靠非 sqlite 通常判定，但本烟测要 sqlite——用 `NARRANEXUS_DEPLOYMENT_MODE=cloud` 显式强制 cloud 分支；若 `_is_cloud_mode` 只认 DATABASE_URL，则改用一个 mysql 测试库或在烟测脚本里 patch。以实际 `backend/auth.py:_is_cloud_mode` 为准。）
2. 前端起 dev server，并注入 NetMind 运行期配置（dev 本地可用 `frontend/.env.local` 的 `VITE_*` 或在 index.html 注入 `window.__NARRANEXUS_CONFIG__`）；`apiUrl` 指向 `http://localhost:8000`。
3. 浏览器打开 `http://localhost:5173/?token=dev-bypass-tester@narra.dev`。
4. **期望**：URL 里的 token 立即消失，页面进入已登录态（user_id 形如 `devbp_<hash>`），不经过 NetMind 任何调用。这验证了 `?token=` 入站 → netmind-login → 换发 JWT → configStore 登录态 的完整链路。
5. 也可在登录页直接走「邮箱密码」——但那一步会真打 NetMind emailLogin，本地墙内会超时，属预期（真实往返留到 dev EC2）。

`devBypass.integration.test.ts` 是这条链路的契约单测（前端把 `dev-bypass-<email>` 原样作为 `netmind_token` 发给后端）。

---

## 9. 涉及面与 mirror md（铁律 10）

| 文件 | 动作 |
|---|---|
| `frontend/src/lib/netmindAuth/*`（5 文件） | 新增（+各自 mirror md） |
| `frontend/src/stores/configStore.ts` | 改（+mirror md 更新） |
| `frontend/src/lib/api.ts` | 改（+mirror md 更新） |
| `frontend/src/pages/LoginPage.tsx` | 大改（+mirror md 更新） |
| `frontend/src/App.tsx` | 改（+mirror md 更新） |
| `frontend/src/lib/runtimeConfig.ts` | 改（增 4 键，+mirror md） |
| `frontend/src/pages/RegisterPage.tsx` | 删（+删 mirror md） |
| `frontend/package.json` | 加 `crypto-js` + `@types/crypto-js` |
| 部署仓库 `docker/entrypoint-frontend.sh`、`.env.example` | 改（运行期注入 4 键） |

---

## 10. 外部依赖（不阻塞编码，阻塞真实验收/上线）

| 依赖 | 状态 | 影响 |
|---|---|---|
| NetMind 给我们域名开 CORS | 待 Power 配置 | 不开则浏览器直连 emailLogin 被拦——真实登录失败（dev-bypass 自测不受影响） |
| NetMind 注册页规范 URL | 待 Power 告知 | Sign up 外链目标；暂用占位 |
| OAuth auth.html postMessage origin 校验 | 待确认 | 若校验 opener origin，需把我们域名加白 |
| 部署 dev EC2 | 待 | 真实端到端验收场所 |

---

## 11. 与现有迁移设计的关系

存量用户迁移（含手工 one-to-one 变体）是**后端**事项，已在 `phase1-user-system-unification.md` §3.5 / §3.5.1 设计，本次前端不涉及。迁移执行整体推迟（依赖 NetMind email→userSystemCode 通道）。
