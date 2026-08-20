---
code_file: src/xyz_agent_context/agent_framework/loop/broker_client.py
stub: false
last_verified: 2026-08-19
---

## 2026-08-19 — ensure 携带 `allow_stale_replace`:谁有权毁掉一个容器

broker 的 stale 镜像懒替换是**第二个**会掐死在途 run 的杀手(第一个是空闲
回收器,见 [[executor_reaper.py]])。它原来的注释假设"部署刚重启过 orchestrator,
在途 loop 已被掐断" —— 两处都错:替换是**懒**的(在用户下一次 ensure 时才发生,
可能是部署后几小时;2026-07-31 prod 那天 07:40–09:14 一共替换了 13 次),而
orchestrator 是 backend + workers **两个进程**,重启 API server 掐不断 workers
里的 run。

**判决权放在编排侧,不放在 broker**:broker 是全系统唯一有 docker 权限的组件,
它的威胁模型建立在"只有一个受调用方控制的输入(会被校验的 user_id)"之上。为了
一个编排侧本来就握着的事实给它发 DB 凭证,是拿安全面换便利。所以判决在**编排侧**算(`agent_runtime/executor_reaper.stale_replacement_is_safe`,
由 [[step_3_agent_loop.py]] 调用),本文件只是 transport,收一个
`allow_stale_replace` bool 往线上发。跟 reaper 的 `is_busy` 注入同形状。

**字段名只管 stale 这一条替换路径**:broker(尤其 deploy 仓 `main`)还有别的
替换理由——健康探测失败、容量 churn。那些**必须无条件放行**:一个已经拨不通的
容器上没有可被打断的 run,"不确定就别动它"在那里不适用,拦下来反而让用户卡在
死容器上且无自愈。名字写成 `allow_stale_replace` 就是为了让下一个加替换理由的
人不必重新猜这个 flag 管不管他。

**必须排除自己那条 run**:ensure 发生在 step 3,那时本 run 的 events 行早就是
`running` 了。不排除的话判决恒为"忙",镜像**永远滚不动** —— 那就是把一种静默
故障换成另一种(2026-07 mcp_servers 改名那次,旧 executor 拿到空 MCP 集,不报错)。
`user_has_live_run(exclude_run_id=...)` 就是为这个加的。

**判决可以偏保守**:同用户的另一条 live run 未必真在用 executor(可能还没走到
step 3,或是压根不碰 executor 的 direct trigger)。多等一轮的代价是这一轮跑在旧
镜像上,下次 ensure 自动纠正;判错方向则是掐死一条在跑的 run(铁律 #14)。
算不出来(DB 不可达)一律不替换。

**不静默**:broker 推迟替换时回 `stale_replace_deferred: true`,两侧都打日志
(这边是 warning)。默认值定在 broker 侧的 `False`,理由见 deploy 仓
`broker/broker.py` 的 `EnsureReq.allow_replace` 注释:两个仓分开部署,默认必须
让版本错配只造成"延迟滚镜像",而不是"422 掉每一次 run"或"继续杀 run"。

## 2026-08-11 — `executor_healthy` 转公开（去重健康探测）

`_executor_healthy` 升为公开 `executor_healthy(executor_url, *,
timeout=5.0)`：吃 executor **base URL**、自己拼 `/health`，永不 raise。
动机：prewarm 路由（`backend/routes/channels/narramessenger.py`）此前
私有不可导而复刻了一份同逻辑——现在全仓只有这一个探针。`timeout` 参数
化是给热路径用的（prewarm 双端点传 1.0，铃响中不能被卡死容器拖满默认
5s）；`wait_until_ready` 内部改调它（默认 timeout），测试的 monkeypatch
seam 名字随之是 `executor_healthy`。

## 2026-08-10 — ensure 响应透传 identity_token（MCP caller auth）

`ExecutorEnsureResult` 新增 `identity_token`(默认 None):broker 用私钥签的
per-user Ed25519 token,**每次 ensure 都新鲜**(暖复用也返新 token,这正是选
「ensure 响应」而不是「executor env 注入」的原因——容器跨 run 长活,env 只在
创建时注入,短 TTL 会过期)。旧 broker 响应缺字段 → None,dispatch 侧不 stamp,
无 lockstep 硬依赖。消费方:[[step_3_agent_loop.py]] `_dispatch_identity_token`。

## 2026-07-31 — executor_seam_active():「本进程不跑 CLI」的单一判据

新公开函数:BROKER_URL(dev/prod compose 实际设的)**或**静态
AGENT_EXECUTOR_URL 任一存在即 True。给 verify_live 的控制面守卫用——
凡是拿本地状态(PATH、~/.codex、~/.claude)下 CLI/凭证判决的代码,
seam 生效时必须判"本节点无法决定"。教训:PR #224 第一版守卫只认
AGENT_EXECUTOR_URL,而部署仓从不设它,守卫在云上是死代码。

## 2026-07-22 — broker/冷启动不可达 → 类型化 ExecutorUnreachableError

两处改为抛类型化异常（[[executor_errors.py]]）：
- `ensure_executor`：httpx 传输错误（`httpx.TransportError`）→
  `ExecutorUnreachableError`。broker 的 HTTP status 错误**不**转换（照旧上抛）。
- `wait_until_ready`：容器超时未就绪，`RuntimeError` → `ExecutorUnreachableError`。

目的：冷启动阶段的不可达也能被上层 [[step_3_agent_loop.py]] 按类名 surface 成
`infra_transient` 可读错误（配合 step_3 把 try 边界上移到 ensure/warm 之外）。

## 2026-06-18 — wait for cold-started executors before driving

`ensure_executor` returns as soon as the broker `docker run`s the container —
it does NOT wait for uvicorn on :8020. So a cold start (`cold_started=True`)
returns a not-yet-ready URL; connecting immediately races the boot and the run
wrongly drops into the fallback path. New `wait_until_ready(executor_url)`
polls the executor's `/health` (via `executor_healthy` — public since
2026-08-11, still the monkeypatch seam for tests) until 200 — condition-based, not a fixed sleep, and NOT an agent-loop
cap (rule #14); it only waits for infra. step_3 calls it on cold start, right
after emitting the `executor.warming` UX event and before driving the loop.
Raises if the container never comes up within the timeout (genuinely broken).

## 为什么存在

orchestrator 侧调用 Executor Broker(部署在 deploy 仓库 `broker/`)的薄客户端。
云端每个用户的 agent-loop 跑在 broker 起的 **per-user Executor 容器**里(只挂该
用户 workspace、无平台密钥)。executor URL 因此**按用户动态**——本模块通过让
broker"确保该用户 executor 在跑"来现取它的 URL。

## 关键点 / 坑

- **API**:`ensure_executor(user_id) -> ExecutorEnsureResult | None`,带 `url` +
  `cold_started`(broker 返回 status=="started" 即冷启动)。`cold_started` 驱动
  前端"唤醒"UX(见下)。
- **`BROKER_URL` 门控**:只有云端 orchestrator 设它。未设(本地/桌面,或旧的
  单 executor 静态 `AGENT_EXECUTOR_URL` 模型)→ `ensure_executor` 返回
  `None`,调用方(step_3 → `get_agent_loop_driver`)回退。所以这是**附加且向后
  兼容**的。
- **唤醒 UX**:`cold_started` 时 step_3 发 `ProgressMessage(step="executor.warming",
  running)`,醒来第一个事件前发配对 `completed`;前端 `WakingOverlay` 据此虚化
  聊天面。见 `[[../../../../frontend/src/components/chat/WakingOverlay.tsx]]`。
- **冷启动触发点**:`broker.ensure` 可能拉起一个容器(数秒),故 timeout 放宽
  (120s),且 run 启动流程要向前端发"正在唤醒"状态(见 handoff 文档的唤醒 UX)。
- **失败要响**:broker/传输出错时**抛**异常,不静默回退到进程内 spawn——那会
  破坏隔离。云端宁可这一次 run 失败并暴露错误。
- 调用链:`step_3 → ensure_executor(user_id) → get_agent_loop_driver(executor_url=...)
  → RemoteAgentLoopDriver(该用户容器)`。executor 看到的 workspace 路径与
  orchestrator 一致(两边 BASE_WORKING_PATH 都是 `/opt/narranexus/workspaces`,
  nested 布局 `{user}/{agent}`),故 `working_path` 直接透传,无需翻译。
- `stop_executor(user_id)`:DELETE /executors/{user},供 idle-cull 用(见
  [[executor_reaper.py]])。同样 `BROKER_URL` 门控(未配置则 no-op);出错抛给
  reaper,reaper 记录并跳过,broker label-based reaper 兜底。
