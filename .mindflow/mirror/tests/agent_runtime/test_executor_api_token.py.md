---
code_file: tests/agent_runtime/test_executor_api_token.py
stub: false
last_verified: 2026-10-29
---

# tests/agent_runtime/test_executor_api_token.py

钉住执行器 API token（沙箱隔离阶段 3）的两端契约：执行器无 token / 错 token 一律 401、对的 token 放行、`/health` 永远开放；编排侧 broker client 解析 `executor_token`、远程驱动把它挂在 session 上（`/agent-loop` 与 `/steer` 共用）、驱动工厂只把它给远程驱动、step 3 的 url 与 token 同源、office_watch 代理每一跳都带。

**发布安全性也是契约的一部分**，同样在这里钉住：执行器 env 无 token 时不校验、调用方没拿到 token 时不发——所以 deploy 仓 broker 与子模块哪个先上都不会 401。broker 侧的种 token / 复用读回 / 无 token 容器滚动替换由 deploy 仓 `broker/test_broker_executor_token.py` 钉住。
