---
code_file: tests/agent_runtime/test_executor_api_token.py
stub: false
last_verified: 2026-10-29
---

# tests/agent_runtime/test_executor_api_token.py

钉住执行器 API token（沙箱隔离阶段 3）的两端契约：执行器无 token / 错 token 一律 401、对的 token 放行、`/health` 永远开放、不带 token 的 websocket 被 close 1008（只有 lifespan 放行）、token 中间件在 `user_middleware[0]`（最外层，被拒请求不算 in-flight）、token 读出后从 `os.environ` 移除（agent 子进程继承不到）；编排侧 broker client 解析 `executor_token`、远程驱动把它挂在 session 上、执行器回 401 时抛 `ExecutorAuthError` 并被归类为执行器基础设施故障（不被兜底盖住）、驱动工厂只把它给远程驱动、step 3 经 `_build_turn_driver` 构造驱动且函数体确实走这个 seam、office_watch 代理 `/watch/ensure`、`/watch/version` 以及透传 GET/POST 都带（本地分支不带）。

**每条都做过变异验证**：分别删掉 step 3 传 token、代理 GET/POST 合并 auth、env pop、401 类型化、websocket 校验、驱动 session headers 这 7 处实现，对应测试都会变红。

**发布安全性也是契约的一部分**，同样在这里钉住：执行器 env 无 token 时不校验、调用方没拿到 token 时不发——所以 deploy 仓 broker 与子模块哪个先上都不会 401。broker 侧的种 token / 复用读回 / 无 token 容器滚动替换由 deploy 仓 `broker/test_broker_executor_token.py` 钉住。
