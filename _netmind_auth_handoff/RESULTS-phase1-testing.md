# Phase 1 测试结果回执（本地 CC 执行）

> 执行：Bin 哥本地的 Claude Code（能连 NetMind dev 端点）
> 日期：2026-06-11
> 对应：`HANDOFF-testing-phase1.md`
> 一句话结论：**核心 auth 契约扎实，没有发现真 bug。** 问题集中在「文档配方」和「本地环境」的小坑上（见 §3）。

---

## 0. 测法说明（和 handoff 不同的地方）

handoff 想要的是「起前后端 + 浏览器 ?token= 烟测」。本地两个现实约束：

1. `_is_cloud_mode()` 只认 `DATABASE_URL` 非 sqlite（**不读** `NARRANEXUS_DEPLOYMENT_MODE`，见 §3.1），sqlite 下 `netmind-login` 直接 404。
2. 本机无 docker，起不了 MySQL 跑「真 cloud 语义」的整站。

所以改成 **直接验接口 + 单元/集成测试** 这条更快更忠实的路径，覆盖面其实比浏览器烟测更全：
- 用 Node 复刻前端 DES 协议，**用真测试账号直接打 dev 端点**（服务端直连，天然绕开浏览器 CORS）。
- 用真 loginToken 喂给**后端 Python `verify_token`**，验证后端解析。
- 跑后端 pytest（route 的 upsert+JWT+cloud 门控）+ 前端 vitest（?token= 入站 / devBypass / 交换契约）。

复跑脚本：`_netmind_auth_handoff/netmind_probe.mjs`（用法见文件头）。

---

## 1. 验证通过的（真账号 + 真端点 + 前后端双向）

dev 端点 `https://userauth.protago-dev.com`，sysCode `f925fc2c`，3 个 test 账号（密码 `123123aA!`）：

| 账号 | emailLogin | /user/balance → userSystemCode |
|---|---|---|
| 13924451750@163.com | ✅ 200 loginToken(325) | ✅ `32b2d09005262cdb8371f7f6f68ae883`（nickname=1750song2） |
| 15627310563@163.com | ✅ 200 loginToken(327) | ✅ `ae65b8e16c8d42aab802d816500da337` |
| gzchao2@163.com | ✅ 200 loginToken(327) | ✅ `8773c1b299804d68930e783b9dcc5c8a`（nickname=gzchao2new） |

- **DES 协议字节正确**：`DES-CBC(key=iv=signStr, PKCS7, hex)` + 明文 signStr + `ckType=2` + form-urlencoded + baseRequestParams(deviceId/clientType/clientVersion/sysCode)。
- **后端 `NetmindAuthClient.verify_token`（真 token）** 解析出的 `user_system_code` 与前端/Node **逐字一致**（`32b2d0…883`）。`/user/balance` 自定义头 `token: Bearer <loginToken>` 正确。
- **userSystemCode 是 32-hex**，与文档一致 → 作为我们的 user_id。

### 错误语义（实测 NetMind 真实返回）

| token 类型 | NetMind 返回 | 我们映射 | 评价 |
|---|---|---|---|
| 过期/被篡改的**真 NetMind token** | `200 {success:false, msg:"token expired", errorcode:"NOT_LOGGEDIN"}` | → **401** | ✅ 正确——这是真实用户会遇到的情况，client 第 139 行 `success is False` 命中 |
| 外来/垃圾 JWT（非 NetMind 签发） | `500`（`org.apache.shiro.authc.AuthenticationException`） | → 502 | ⚠️ 见 §3.4，正常流程打不到 |

### 自动化测试

- **后端 pytest 22/22 green**：`test_netmind_login_route.py`（happy path 发自家 JWT、quota seed once、quota 失败不阻塞登录、invalid→401、upstream→502、cloud-only 404、auth-exempt）+ `test_netmind_auth_client.py` + `test_legacy_auth_removed.py`（密码登录已移除、local 登录仍在、register 路由已删）。
  - 注意跑法：`PYTHONPATH=src:. uv run pytest ...`（见 §3.3）。
- **前端 vitest 15/15 green**：`tokenInbound`（?token= 即取即删）、`devBypass.integration`、`api.netmindLogin`（POST 契约）、`crypto`、`useNetmindAuth`。

---

## 2. 没验的（依赖外部，非代码问题）

- **档 B 浏览器真账号往返 + CORS**：本次用服务端直连证明了协议与前后端实现都对；CORS 纯粹是浏览器侧，要 Power 给我们域名开（handoff §5 已记）。**这不是我们代码的事**。
- **OAuth 弹窗 postMessage**：未测（需浏览器 + accounts 域）。
- **注册页 URL**：仍待 Power 给，"Create Account" 外链暂空——符合预期。

---

## 3. 发现的问题（按重要性，给你判断要不要改）

### 3.1 [文档坑·建议改] HANDOFF §3.1 的本地烟测命令照抄跑不通

`backend/auth.py` 的 `_is_cloud_mode()` 逻辑：
```python
db_url = os.environ.get("DATABASE_URL", "")
if db_url:
    return not db_url.startswith("sqlite")   # sqlite → False（local）
return bool(os.environ.get("DB_HOST", ""))   # 兜底
```
**它完全不读 `NARRANEXUS_DEPLOYMENT_MODE`。** 所以 handoff §3.1 那条 `DATABASE_URL=sqlite... NARRANEXUS_DEPLOYMENT_MODE=cloud` 命令，`_is_cloud_mode()` 仍判 local → `netmind-login` 返回 404（路由 `backend/routes/auth.py:174`）。而且 `DATABASE_URL` 一旦是 sqlite 就提前返回，`DB_HOST` 兜底也轮不到。

**建议二选一**：
- (a) 给 `_is_cloud_mode()` 加一个显式 env 覆盖（如 `NARRANEXUS_DEPLOYMENT_MODE=cloud` 强制 True），让 sqlite+cloud 语义可本地烟测；
- (b) 或在 handoff 里改成「本地验证走 pytest route 测试（已 monkeypatch cloud 模式）」，不要承诺 sqlite 能起 cloud 烟测。

### 3.2 [本地环境·已修] `frontend/node_modules` 不全，缺 `crypto-js`

`package.json` 声明了 `crypto-js@^4.2.0`，但 node_modules 里没有 → `crypto.ts` import 失败 → **`npm run dev` 进登录页会挂、前端 vitest 2 个文件 import 报错**。
- 已 `npm install crypto-js` 修复（**未改 package.json / package-lock.json**，只是按 lockfile 补回 node_modules）。
- **任何人新 checkout 这分支，先 `npm install` 再说。**

### 3.3 [本地环境·小坑] 裸 `uv run pytest` 起不来

`pyproject.toml` 的 `[tool.pytest.ini_options]` 没设 `pythonpath`，包也没 editable 安装 → conftest `import xyz_agent_context` 失败；route 测试还要 import `backend.routes`。
- 正确跑法：`PYTHONPATH=src:. uv run pytest ...`，或先 `uv sync` 做 editable 安装。

### 3.4 [轻微边角·可选] 垃圾 `?token=` → 502 而非 401

非 NetMind 签发的乱码 JWT 会让 NetMind `/user/balance` 回 500（Shiro AuthException），我们映射成 502「服务不可用」。真实过期 token 走的是 `200{success:false}` → 已正确 401，所以正常流程打不到这条。若想加固：可在 `verify_token` 对 500 且响应体含 `NOT_LOGGEDIN`/认证类异常时也判为 NetmindAuthError(401)。优先级低。

---

## 4. 给你的最短行动清单

1. §3.1：决定 `_is_cloud_mode()` 加 env 覆盖，还是改文档措辞。
2. §3.2：确认是不是该把 node_modules 补全 / 或在文档强调 `npm install`。
3. §3.3：考虑给 pytest 配 `pythonpath = ["src", "."]`，省得每次手设。
4. §3.4：可选加固，不急。

协议层和前后端实现本身**不用改**——真账号实测全通。
