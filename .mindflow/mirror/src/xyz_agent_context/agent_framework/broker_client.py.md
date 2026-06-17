---
code_file: src/xyz_agent_context/agent_framework/broker_client.py
stub: false
last_verified: 2026-06-17
---

## 为什么存在

orchestrator 侧调用 Executor Broker(部署在 deploy 仓库 `broker/`)的薄客户端。
云端每个用户的 agent-loop 跑在 broker 起的 **per-user Executor 容器**里(只挂该
用户 workspace、无平台密钥)。executor URL 因此**按用户动态**——本模块通过让
broker"确保该用户 executor 在跑"来现取它的 URL。

## 关键点 / 坑

- **`BROKER_URL` 门控**:只有云端 orchestrator 设它。未设(本地/桌面,或旧的
  单 executor 静态 `AGENT_EXECUTOR_URL` 模型)→ `resolve_executor_url` 返回
  `None`,调用方(step_3 → `get_agent_loop_driver`)回退。所以这是**附加且向后
  兼容**的。
- **冷启动触发点**:`broker.ensure` 可能拉起一个容器(数秒),故 timeout 放宽
  (120s),且 run 启动流程要向前端发"正在唤醒"状态(见 handoff 文档的唤醒 UX)。
- **失败要响**:broker/传输出错时**抛**异常,不静默回退到进程内 spawn——那会
  破坏隔离。云端宁可这一次 run 失败并暴露错误。
- 调用链:`step_3 → resolve_executor_url(user_id) → get_agent_loop_driver(executor_url=...)
  → RemoteAgentLoopDriver(该用户容器)`。executor 看到的 workspace 路径与
  orchestrator 一致(两边 BASE_WORKING_PATH 都是 `/opt/narranexus/workspaces`,
  nested 布局 `{user}/{agent}`),故 `working_path` 直接透传,无需翻译。
- `stop_executor(user_id)`:DELETE /executors/{user},供 idle-cull 用(见
  [[executor_reaper.py]])。同样 `BROKER_URL` 门控(未配置则 no-op);出错抛给
  reaper,reaper 记录并跳过,broker label-based reaper 兜底。
