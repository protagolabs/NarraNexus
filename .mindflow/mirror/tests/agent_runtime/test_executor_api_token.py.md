---
code_file: tests/agent_runtime/test_executor_api_token.py
stub: false
last_verified: 2026-10-29
---

### 2026-10-29（管线审查复核）— never silent / never leaked

新增 5 条契约（各自做过变异验证）：
- `/health` 带 `token_enforced`（只给布尔）；测试进程里 conftest 已清掉 token，故断言为 False，True 那一侧由全新解释器测试钉住（变异：去掉该字段 → 红）。
- broker 返回缺 `executor_url` 时，报错文案不含 `executor_token` / `identity_token` 的值、但仍列出键名（变异：改回整个响应 → 红）。
- ensure() 拿不到 executor_token 时每进程只 warning 一次（变异：去掉告警 → 红）。
- `executor_errors` 里的**每一个**异常类都必须在 `EXECUTOR_INFRA_ERROR_NAMES` 里、且被分类为 infra（内省式；变异：从名单拿掉一个 → 红）。
- office-watch 的透传 GET / 透传 POST 两跳在执行器拒绝 token 时也留 error 日志（ensure / version 两跳已有；变异：去掉任一透传日志 → 红）。

# tests/agent_runtime/test_executor_api_token.py

钉住执行器 API token（沙箱隔离阶段 3）的两端契约：执行器无 token / 错 token 一律 401、对的 token 放行、`/health` 永远开放、不带 token 的 websocket 被 close 1008、带对 token 的 websocket 放行（只有 lifespan 免检）、token 中间件在 `user_middleware[0]`（最外层，被拒请求不算 in-flight）、token 读出后从 `os.environ` 移除（agent 子进程继承不到）——既测 helper，也在全新解释器里真实 import `executor_service` 断言生产接线确实 pop 了且中间件拿到了 token；编排侧 broker client 解析 `executor_token`、远程驱动把它挂在 session 上、执行器回 401 时抛 `ExecutorTokenRejectedError` 并被归类为执行器基础设施故障（不被兜底盖住）、驱动工厂只把它给远程驱动、step 3 经 `_build_turn_driver` 构造驱动且函数体确实走这个 seam、office_watch 代理 `/watch/ensure`、`/watch/version` 以及透传 GET/POST 都带（本地分支不带）。

**每条都做过变异验证**：分别破坏 step 3 传 token、代理 GET 合并 auth、代理 POST 合并 auth、env pop（helper 与生产调用点各一）、401 类型化、websocket 校验（拒绝与放行各一）、驱动 session headers，对应测试都会变红。

**发布安全性也是契约的一部分**，同样在这里钉住：执行器 env 无 token 时不校验、调用方没拿到 token 时不发——所以 **deploy 仓 broker 与子模块任意顺序上线都安全**。限定：**本仓自己的部署窗口内**，旧编排进程可能撞上已被 broker 滚到新镜像（开始校验）的执行器，那一回合会 401——它是类型化的 `ExecutorTokenRejectedError`、写审计、以 `infra_transient` 告知用户，编排进程换上新镜像后下一回合自愈。broker 侧的种 token / 复用读回 / 无 token 容器滚动替换由 deploy 仓 `broker/test_broker_executor_token.py` 钉住。
