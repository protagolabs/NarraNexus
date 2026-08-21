---
code_file: src/xyz_agent_context/agent_framework/nexus_power/runner.py
last_verified: 2026-08-21
stub: false
---

## 2026-08-21 — serve_turn 转发 steering inlet

`serve_turn` 新增 `steering` 关键字参,原样转给 `run_turn_events`;`None` 时行为不变。
类型标 `SteeringInlet | None`,经 `TYPE_CHECKING` 懒导入(不进模块顶层,守 `_prewarm` 冷启动
契约)——这样 transport 调用方能被 Protocol 静态校验,不至于误传裸 `asyncio.Queue`。进程宿主
负责构造并喂这个 inlet——inlet 是活对象,**不跨序列化边界**(见 [[assembly.py]] 同日条目)。
本 PR 只加转发;真正让 stdin 续读 steer 行喂进 inlet 的 transport 是后续改动。

# runner — 独立进程宿主(一个协议两种传输)

Owner 拍板:agent 回合独立进程跑。云端=executor 容器 HTTP;本地=driver spawn 本模块,stdin 一行 TurnRequest JSON、stdout NDJSON({event}/{exit})。行长无上限假设——读端必须手动缓冲(2026-07-08 aiohttp 128KiB 行读上限事故)。SIGTERM/断流→协作取消(配对不变量保住)。每回合事件同时落 <cwd>/.nexus_power/<thread>.ndjson 本地真相文件(C1)。exit 错误带 traceback 尾巴(排障实测必须)。

## 2026-07-29 — NEXUS_POWER_PREWARM

池化契约:PREWARM=1 时在阻塞读 stdin 之前吃掉全部导入(assembly 图 +
litellm),温进程的首 token 不含任何导入成本。
