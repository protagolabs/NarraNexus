---
code_file: src/xyz_agent_context/agent_runtime/executor_service.py
stub: false
last_verified: 2026-08-20
---

## 2026-08-20 — /health 报 `busy`:容器自己回答"能不能停我"

`/health` 多两个字段:`busy` / `inflight_work`。消费方是 **broker 的 idle
reaper**(deploy 仓 `broker._executor_busy_state`)——这是一条跨仓契约,改
`/health` 的返回或挪动计数的位置,会静默地把 broker 的"别停正在干活的容器"
护栏解除。

**为什么必须有**:broker 只看得见 turn **START**(ensure() 每轮调一次),之后
backend 直接对容器流式。所以它的 `_last_seen` 量的是"距这轮开始多久",一条
比 idle TTL 更长的 turn 和一个被遗弃的容器长得一模一样。铁律 #14 说多小时的
turn 是一等场景,所以"把 TTL 调大"不是答案。

**为什么由容器回答,而不是查编排侧的 DB**:对"能不能停这个容器"来说,容器
自己就是精确的问题和精确的答案——没有 Step-0 写入窗口,也不依赖 run recording
有没有被关掉。(stale **镜像**替换反过来仍用编排侧判决,见
[[broker_client.py]]:那里的容器按定义跑着旧镜像,可能根本没有这个字段,
探它会永远答"说不准",镜像就永远滚不动。)

**计数为什么在 ASGI 中间件里,不在 handler 里**:
`StreamingResponse.stream_response` 先 `send(http.response.start)`,**之后**
才碰 `body_iterator`。消费者已经断开时那个 send 抛异常,生成器**从未启动**,
而关闭一个从未启动的 async generator 不执行任何代码——包括 `finally`。
handler 里的计数在这条路径上**永久泄漏**:容器此后一直报 busy → reaper 永远
拒绝回收 → 槽位再也回不来,且所有指标都看不见(已对 starlette 0.50 /
uvicorn 0.38 实测复现)。中间件把计数括在 `await self.app(...)` 两侧,所有
退出路径都经过它。用裸 ASGI 而不是 `@app.middleware("http")`:后者是
`BaseHTTPMiddleware`,会把流式 body 再through 一层 anyio memory stream,
等于给每个 NDJSON 帧多拷一份——而这条路径的帧能到几百 KiB。

**`busy` 的定义是"这个容器在被使用",不只是"有 agent turn 在流"**:
`_WORK_PATH_PREFIXES` 含 `/watch`,因为 office-watch 端点代理的是**跑在本
容器内**的服务,在它下面回收会一并杀掉那个会话。`/health` 必须留在集合外,
否则 broker 的探测会自我实现。

**已知边界**:计的是**请求**,不是会话。一个空闲的 office-watch 会话(没有
请求在飞)仍然报 not busy。今天被"每次 proxy 调用都会刷新 broker 的
`_last_seen`"掩盖着,真要修需要容器内的会话注册表,不在本次范围。


## 2026-08-19 — 执行器侧读取并转发 origin_declaration

`_stream` 调 `driver.agent_loop(...)` 时新增 `origin_declaration=body.get("origin_declaration") or ""`。键名与 `build_agent_loop_request` 写死一致（两处独立构造的隐患由测试断言防住）。

## 2026-08-10 (review 修正) — 字段改名 `extra_readable_roots` → `extra_accessible_roots`

纯改名，语义不变：这份授予同时管写与删（confinement 层检查 `file_path` 与 shell 路径），
旧名名不副实。详见 [[policy.py]]。

## 2026-08-07 — 从 body 取出 `extra_readable_roots` 交给 driver

与 [[remote_driver.py]] 对称的另一端。白名单 body 的两端必须成对改，否则字段在网络边界
被静默丢弃。

## 2026-08-06 — voice fast mode: TurnProfile 管道（缺省=现状）

/agent-loop 端点把 body["turn_profile"] 透传给容器内 driver。

## 2026-07-31 — 回复契约:投递面由平台声明(expressive seam)

`/agent-loop` 处理器把 body 的 `agent_id` / `expressive_tools` 转发给本地
driver(此前只转 4 个 kwargs,声明会在云端被丢弃)。

## 2026-07-29 — 不再授权 resume 句柄(T6)

删掉 `authorize_resume_session_id(body)` 这一步及传给 driver 的
`resume_session_id=` 参数。理由见 [[executor_protocol]] 同日条目。

值得记一笔的是**为什么 executor 侧不需要任何新增**:这里传的是
`messages=body["messages"]`,历史本来就在里面;容器内起的是**本地 driver**(即同一个
claude adapter),它自己 `split_for_argv` 拿到 history_entries、自己在容器文件系统里
写 transcript。原计划(T3)以为要新加 `transcript_turns` 协议字段,查证后不需要。

关键是这不是巧合:transcript 路径的 slug 与 CLI 的 `options.cwd` **派生自同一个变量**
(`self.working_path`),所以两者在任何环境下自动一致。

## 2026-07-28 — resume 句柄改为经 HMAC 校验后才采信（HIGH review finding）

`/agent-loop` 里 `resume_session_id` 不再直接透传，改为
`authorize_resume_session_id(body)`（见 [[executor_protocol.py]] 同日条目）。
原因：本端点**无鉴权是刻意设计**，但这只在"body 每个字段只描述本次请求"时成立；
resume 句柄指向的是**全租户共用** `CLAUDE_CONFIG_DIR` 里的 CLI transcript，配上
可猜的 `working_path`（`{base}/{user_id}/{agent_id}`），直连本端点即可读回别人的
对话。现在 orchestrator（真正做过 per-user 校验的一侧）签名，本端常量时间校验。

关键行为：**校验失败一律降级为冷启动，绝不 4xx**。resume 是优化，冷启动永远正确；
把签名/时钟/部署问题变成 turn 失败才是真的坏。端点整体仍然是无鉴权的内网信任面
——这里鉴权的是**一个能力**，不是整个请求。

**云端部署依赖**：`EXECUTOR_RESUME_HMAC_SECRET` 必须注入本容器 env（容器不读平台
.env，所以只能走容器 env），且与 orchestrator 同值；未配置 = 本容器忽略所有
resume 句柄并打一次性 WARNING。

## 2026-07-28 — 透传 body `resume_session_id` 给 driver（resume 化 R2）

`/agent-loop` 把 `body.get("resume_session_id") or None` 传进容器内
driver.agent_loop kwargs，与本地路径（[[step_3_agent_loop.py]] 经 TurnInput
直传）对齐——两条运行模式一起通（铁律 #7），代码不区分本地/云端。纯透传，
无逻辑；executor 容器内没有 DB，resume 失败后的句柄清除在 orchestrator 侧
step_4 做。

## 2026-07-24 — 透传 body `disallowed_tools` 给 driver（setup-residency B++）

executor 端把 `body.get("disallowed_tools") or None` 传进容器内
driver.agent_loop kwargs，与本地路径（[[step_3_agent_loop.py]] 直传）对齐——
remote 路径下未绑定 channel 的工具同样从模型上下文剔除。纯透传，无逻辑。

## 2026-07-15 — MCP 管道改名 `mcp_urls`/`mcp_server_urls` → `mcp_servers`

值类型从 url 字符串升级为 spec 对象 `{"url": str, "headers": {str:str}?}`，
支撑用户 MCP 自定义请求头（Authorization 等）贯穿全链路。本文件仅机械跟随
改名/类型，职责不变。

## 2026-06-17 — 日志写到 user 目录

`main()` 启动时把 loguru 文件 sink 落到**该用户 workspace 目录**下的
`.executor_logs/`(`_resolve_executor_log_dir`:容器只挂了一个 user 子目录,
取那个唯一子目录;取不到则回退 base)。这样每个用户的 executor 日志隔离、
随挂载卷持久化到宿主,便于按用户排查。stderr sink 保留(`docker logs` 仍可用)。
文件日志 best-effort(失败只 warning,不挂服务)。

## Why it exists

The agent-loop **Executor** — a thin FastAPI service that is the ONLY
tier which spawns the claude/codex CLI. Given an assembled prompt + the
resolved (scoped) provider configs + the workspace path, it runs the
LOCAL agent-loop driver and streams the raw event dicts back as NDJSON
(`POST /agent-loop`). This is the data-plane half of the
control-plane/data-plane split (binding rule #20).

## Security shape (the point of extracting it)

- **No platform master secrets.** Started WITHOUT the platform `.env`;
  the only credential it sees is the per-run scoped LLM key, arriving in
  the request body and applied to a ContextVar for the loop's duration.
  So `env` inside the agent shows nothing sensitive, and a compromise of
  this container yields ~nothing persistent.
- **No database.** All DB work (pipeline steps 0-2.5) happened in the
  orchestrator; the executor only runs the loop it's handed.
- **No self-recursion.** The executor container does NOT set
  `AGENT_EXECUTOR_URL`, so `get_agent_loop_driver` resolves to the LOCAL
  claude/codex driver here (the remote driver is only used by the
  orchestrator).

## Gotchas / future

- Streaming is NDJSON: `{"event": {...}}` per line, `{"error": {...}}` on
  failure. The remote driver re-raises on the error line to match
  local-driver exception semantics.
- Raw event dicts are JSON-encoded with `default=str` — if an event
  carries a type that doesn't round-trip cleanly, `ResponseProcessor`
  (orchestrator side) could see a degraded value; watch this when
  flipping the remote path on in prod.
- Per-agent/per-user workspace isolation is a DEPLOYMENT concern layered
  on top (per-user container mounting only `workspaces/{user_id}`) — not
  this module's job. This module just runs the loop it is given.

## 2026-07-13 — office live-preview watch endpoints

新增两个端点支持 office artifact 的实时预览(watch 必须跑在 executor 容器内,因为工作区 + agent 的 officecli 编辑都在这里):
- `POST /watch/ensure` {agent_id,user_id,file} → 在容器内 `ensure_watch`(detached spawn officecli watch),**返回容器为该文件分配到的端口** `{ok,port}`。容器自己拥有端口分配(每文件一个专属端口),后端不再猜端口(改自 2026-07-13:原来是后端 hash 出 port 传进来,多文档并发会串台)。由后端 `/office-watch/open` 云端分支调用,拿到 port 后铸 token。
- `GET /watch/{port}/{path}` → 反代到容器内 `127.0.0.1:{port}` 的 watch 服务(SSE 流式,X-Accel-Buffering: no)。由后端公共代理转发到这里。两者都无鉴权(内网信任,同 /agent-loop),但仍做端口 allowlist 防御纵深。
- `GET /watch/version?agent_id&user_id&file` → 容器内 stat office 文件返回 `{mtime,size}`,给前端 mtime 兜底轮询用(云端工作区在容器里,后端 stat 不到)。由后端 `/office-watch/version` 云端分支调用。
