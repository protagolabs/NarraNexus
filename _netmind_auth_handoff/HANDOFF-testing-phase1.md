# Phase 1 测试交接文档（给能连 EC2 / Power 的本地 CC）

> 写给：Bin 哥本地起的一个 Claude Code（能连 dev EC2 + NetMind protago-dev）
> 写于：2026-06-11
> 背景：实现这套代码的 agent（我）所在工作站**连不上 AWS EC2，也连不上 NetMind 端点**（`userauth.protago-dev.com` / `auth-api.netmind.ai` 均超时），所以真实部署与真账号登录验收没法由我做。请你照本文档执行，把每一步的**实际输出**贴回去。

---

## 0. 你要验证的东西分三档

| 档 | 内容 | 需要什么 |
|---|---|---|
| A. 本地 dev-bypass 烟测 | 验证「我们这侧」全栈打通（?token= → 后端换 JWT → 登录态），**完全不碰 NetMind** | 只要能跑起前后端 |
| B. 真账号登录验收 | 真邮箱密码 / OAuth → 真 NetMind → 我们后端 → 登录成功 | 能连 NetMind protago-dev + Power 已给我们域名开 CORS + 测试账号 |
| C. 部署到 dev EC2 | 把这套部署到 dev EC2 跑起来 | 能 SSH/GH-Actions 桥连 dev EC2 |

**优先做 A**（最快、无外部依赖）。B 依赖 Power 配置 CORS（见 §5），不一定就绪。C 看你想不想真上 dev。

### NetMind 端点对照表（dev/test vs prod）

**测试用 dev/test 这套**（档 A/B/C 都用 dev）。prod 列出来仅供知悉，**现在别用**。

| 用途 | dev / test（现在用这套） | prod（暂不用） |
|---|---|---|
| 后端验 token + 前端登录 API（`NETMIND_AUTH_API_URL`） | `https://userauth.protago-dev.com` | `https://auth-api.netmind.ai` |
| OAuth 弹窗 accounts 域（`NETMIND_ACCOUNTS_URL`） | `https://accounts.protago-dev.com` | `https://accounts.netmind.ai` |
| sysCode（`NETMIND_SYS_CODE`，两边相同） | `f925fc2c` | `f925fc2c` |
| 注册页（`NETMIND_REGISTER_URL`） | 待 Power 给（暂空） | 待 Power 给（暂空） |

- 这些 URL 是 Arena 在用的同一套（我已用 dev 那套 + 真测试账号实测跑通 emailLogin / balance）。
- sysCode `f925fc2c` 是复用 Power 的（Power PM 确认：同 sysCode 才能打通 Power 登录态，token 通用）。
- 注册页 URL 两套都还没拿到——所以 "Create Account" 外链暂时指向空，点了没反应是正常的，等 Power 给。

---

## 1. 代码在哪

- **前端 + 后端代码**：NarraNexus submodule，分支 **`feat/netmind-auth`**（已 checkout 在本地 the NarraNexus repo root）。
  - 基于 `origin/dev`，含后端 7 commit（NetMind 登录端点等）+ 前端 10 commit（`feat(auth-fe): ...`）。
  - `git log --oneline origin/dev..HEAD` 看全部改动。
- **部署管线改动**（档 C 部署才需要，**不在这个仓库**，测试档 A/B 用不到）：部署仓库 NarraNexus-deploy 那边 3 个文件（entrypoint-frontend.sh 注入 NetMind 4 键、compose.yml 加 4 个 NETMIND_ env、.env.example 加 NetMind 段），由 Bin 哥在部署时应用；§6 列了要往实际 `.env` 加的变量。

---

## 2. 关键背景（实现时已实测确认的事实）

- NetMind 的 `emailLogin` + `/user/balance` 我**已经用 GitHub Actions runner 拿真测试账号实跑通**（`13924451750@163.com` / `123123aA!`，dev 环境）：
  - emailLogin 200，密码协议 = DES-CBC（key=iv=signStr，PKCS7，hex）+ `ckType=2`，**不触发 reCAPTCHA**。
  - `/user/balance` 200，结构 `{data:{user:{...userSystemCode...}}}`，userSystemCode 是 32-hex。
- 所以前端代码的协议/字段不是盲写的，契约对过。
- 后端 `/api/auth/netmind-login` 拿 NetMind loginToken → 调 `/user/balance` 验 → upsert 用户（user_id = userSystemCode）→ 换发我们自己的 JWT。

---

## 3. 档 A：本地 dev-bypass 烟测（先做这个）

目的：不依赖 NetMind，验证 `?token=` 入站 → netmind-login → 换 JWT → 登录态 这条链。后端有个 dev-bypass 开关，`dev-bypass-<email>` 形态的 token 会跳过真实 NetMind 调用、直接造一个 `devbp_<hash>` 用户。

### 3.1 起后端（cloud 语义 + dev-bypass）

```bash
cd <NarraNexus repo root>
NETMIND_DEV_BYPASS=1 \
DATABASE_URL="sqlite+aiosqlite:////tmp/nx_smoke.db" \
NARRANEXUS_DEPLOYMENT_MODE=cloud \
JWT_SECRET=dev-smoke-secret \
SYSTEM_DEFAULT_LLM_ENABLED=false \
uv run uvicorn backend.main:app --host 0.0.0.0 --port 8000
```

**注意**：后端 `_is_cloud_mode()` 的判定看 `backend/auth.py`——它可能只认「DATABASE_URL 非 sqlite」。如果用 sqlite 它判成了 local 模式，那 netmind-login 会返回 404（它是 cloud-only）。两个办法：
- (推荐先试) 上面加了 `NARRANEXUS_DEPLOYMENT_MODE=cloud`，先看它认不认；
- 若仍判 local：读 `backend/auth.py` 的 `_is_cloud_mode()`，确认它的判定依据，必要时临时指一个 mysql 测试库，或在烟测里 monkeypatch。**把你看到的 `_is_cloud_mode` 实际逻辑贴回来**，我来判断。

先用 curl 直接验后端这一层（不用前端）：
```bash
curl -s -X POST http://localhost:8000/api/auth/netmind-login \
  -H 'Content-Type: application/json' \
  -d '{"netmind_token":"dev-bypass-smoke@narra.dev"}' | python3 -m json.tool
```
**期望**：HTTP 200，JSON 含 `success:true`、`user_id` 形如 `devbp_xxxx`、`token`（一个 JWT）、`is_new_user:true`。
**贴回**：这条 curl 的完整输出。若是 404 → 是 cloud 判定问题（见上）；若 500 → 贴后端日志。

### 3.2 起前端 + 注入 NetMind 运行期配置

前端读 `window.__NARRANEXUS_CONFIG__`。本地开发可在 `frontend/.env.local` 放 VITE 兜底，但更简单是直接在浏览器/或临时改 `frontend/index.html` 注入。最省事：建 `frontend/public/config.js`（dev server 会 serve）内容：
```js
window.__NARRANEXUS_CONFIG__ = {
  mode: "cloud",
  apiUrl: "http://localhost:8000",
  netmindAuthApi: "https://userauth.protago-dev.com",
  netmindAccountsUrl: "https://accounts.protago-dev.com",
  netmindSysCode: "f925fc2c",
  netmindRegisterUrl: ""
};
```
确认 `frontend/index.html` 有 `<script src="/config.js"></script>` 在主 bundle 之前（部署时是 entrypoint 写的；dev 下你可能要手动确认 index.html 引了它，没有就加一行）。

起前端：
```bash
cd frontend && npm run dev
```

### 3.3 浏览器验 ?token= 入站

打开：`http://localhost:5173/?token=dev-bypass-smoke@narra.dev`
**期望**：
1. 地址栏里的 `?token=...` 立刻消失（被即取即删）。
2. 页面进入**已登录态**（不是登录页），侧边栏显示一个用户（user_id 形如 `devbp_...`）。
3. 整个过程后端日志里**没有**对 NetMind 的任何调用（dev-bypass 跳过了）。

**贴回**：成功截图/描述；若失败，贴浏览器 console 报错 + Network 里 `/api/auth/netmind-login` 请求的 request body 和 response。

### 3.4 （可选）登录页本身渲染

打开 `http://localhost:5173/login`，**期望**看到：邮箱输入 + 密码输入 + "Sign In" + 三个 OAuth 按钮（Google/Microsoft/GitHub）+ "Create Account"（是个外链）。
- 点 "Sign In" 走真 NetMind emailLogin——**本地若连不上 NetMind 会超时/失败，这是预期**（真往返留到档 B/C）。

---

## 4. 档 B：真账号登录验收（需要 NetMind 可达 + CORS）

**前提**：(a) 你的网络能到 `userauth.protago-dev.com`；(b) Power 已给我们域名开 CORS（见 §5，**很可能还没配**——没配的话浏览器直连 emailLogin 会被 CORS 拦，控制台报 CORS 错）。

测试账号（Power 给的 dev）：
- `13924451750@163.com` / `123123aA!`
- `15627310563@163.com` / `123123aA!`
- `gzchao2@163.com` / `123123aA!`

### 4.1 邮箱密码
在登录页（cloud 模式）输测试账号邮箱 + 密码 → Sign In。
**期望**：登录成功进应用。
**贴回**：成功 or 失败（失败贴 console + Network 里 `/user/emailLogin` 和 `/api/auth/netmind-login` 两个请求的状态码与响应）。**特别注意**：若 `/user/emailLogin` 报 CORS 错 → 是 Power 没给我们域名开 CORS，记下来，这是要找 Power 配的。

### 4.2 OAuth（Google/Microsoft/GitHub）
点某个 OAuth 按钮 → 弹窗走 NetMind accounts 域授权 → 回来应登录成功（首次第三方账号可能弹一个绑定对话框，按提示填邮箱/验证码）。
**贴回**：哪个 provider 试了、成功 or 卡在哪一步（弹窗 URL、postMessage 是否回来、绑定对话框是否出现）。

---

## 5. 已知外部依赖（卡档 B/C 的，不是 bug）

| 依赖 | 状态 | 不满足会怎样 |
|---|---|---|
| Power 给 `agent.narra.nexus` / `dev-agent.narra.nexus` / 本地 dev 域名开 **CORS** | **大概率没配** | 浏览器直连 emailLogin 被 CORS 拦，档 B 邮箱登录失败 |
| NetMind 注册页规范 URL | 待 Power 给 | "Create Account" 外链目前指向空（`netmindRegisterUrl=""`），点了没反应——正常，等 URL |
| OAuth auth.html 的 postMessage origin 校验 | 待确认 | 若它校验 opener origin，OAuth 弹窗回调可能被拦 |

**如果档 B 因为 CORS 失败**：这不是我们代码的 bug，是 Power 侧配置。把 console 里的 CORS 报错原文贴回来，我整理成给 Power 的具体配置请求。

---

## 6. 档 C：部署到 dev EC2（可选，想真上的话）

1. 把 §1 的部署管线 3 文件改动应用到 dev EC2 上的部署仓库（review diff 后）。
2. dev EC2 的实际 `.env`（`stacks/narranexus-app/.env`）加上：
   ```
   NETMIND_AUTH_API_URL=https://userauth.protago-dev.com
   NETMIND_ACCOUNTS_URL=https://accounts.protago-dev.com
   NETMIND_SYS_CODE=f925fc2c
   NETMIND_REGISTER_URL=
   NETMIND_AUTH_TIMEOUT_SECONDS=5
   # 想在 dev 上也能 dev-bypass 烟测就加（生产绝不能加）：
   NETMIND_DEV_BYPASS=1
   ```
   注意：`.env` 里任何含 `$` 的值要写成 `$$`（compose 转义，老坑）。
3. submodule 指到 `feat/netmind-auth`（或先把它合进 dev），`make app-build` + `make app-up`（具体按 dev EC2 的部署 memory）。
4. 访问 dev 域名，重复档 A（dev-bypass `?token=`）和档 B（真账号）。
5. backend 起来后注意 healthcheck 窗口——这次没有耗时 migration（users 表语义变了但没改结构），应该不会撞 v1.7.16 那个窗口，但留意一下启动日志。

---

## 7. 我最想要你贴回来的（按优先级）

1. **档 A §3.1 的 curl 输出**（后端 netmind-login + dev-bypass 是否通）——这个最关键，验证我们这侧。
2. **档 A §3.3 的浏览器 ?token= 入站结果**——验证前端入站链路。
3. 后端 `_is_cloud_mode()` 的实际逻辑（如果档 A 出现 404）。
4. **档 B 邮箱登录**结果——尤其是不是 CORS 卡住（决定要不要催 Power）。
5. 任何 console/Network 报错原文。

贴回来后我会根据实际输出判断：是代码要修，还是外部依赖（CORS/注册 URL）要催 Power，还是 cloud 判定要调。
