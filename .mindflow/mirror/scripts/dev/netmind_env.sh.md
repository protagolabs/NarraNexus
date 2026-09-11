---
code_file: scripts/dev/netmind_env.sh
last_verified: 2026-09-09
stub: false
---

# scripts/dev/netmind_env.sh — 源码本地栈的 NetMind 环境（被 dev-local.sh source）

## 为什么存在

B-40 / 上游 NetMindAI-Open/NarraNexus#90 的真实泄漏点：开源"本地版"就是 `bash run.sh`，它 exec `dev-local.sh`；后者过去默认把 Power 登录开在 **protago-dev** 上，且完全不导出 `VITE_NETMIND_*`，vite dev server 于是落回 runtimeConfig.ts 的 protago-dev 兜底——每个源码用户点"用 GitHub 登录"都进 "Netmind AI Test by protagohhz" / accounts.protago-dev.com，真实 NetMind 账号也被送去 dev auth（#89 的 500 疑似同源，未经服务端日志证实）。

## 行为

- `nexus_netmind_env`：`NEXUS_NETMIND_ENV=prod`（默认）或 `dev` 一次性决定后端 6 个变量 + 前端 5 个 `VITE_*`，前后端不可能不一致；已导出的单个变量优先；`NEXUS_DEV_POWER_LOGIN=0` 什么都不导出（纯用户名登录）；未知取值返回非零，dev-local.sh 随即退出。
- `nexus_netmind_env_cmd`：把上述变量渲染成 `export VAR='v'; ` 串。原因：tmux 窗格继承的是 **tmux server** 的环境（server 首次启动时捕获），不是启动器的——只 export 对复用中的旧 server 无效。dev-local.sh 把它拼进 `ENV_CMD`（后端各窗口）和 Frontend 窗口命令，与转发 PATH 同一手法。

## 测试

`tests/scripts/test_dev_netmind_env.py`：干净 bash 里 source 本文件——默认全 prod 且无 protago-dev、`dev` 一致指向 protago-dev、单变量覆盖生效、关 Power 不导出、未知取值中止；并断言 dev-local.sh 把 `NETMIND_ENV` 拼进 `ENV_CMD` 与 Frontend 窗口、脚本本身不再含 protago-dev 端点。
