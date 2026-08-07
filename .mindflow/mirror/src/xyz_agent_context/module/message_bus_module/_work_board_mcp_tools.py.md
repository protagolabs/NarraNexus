---
code_file: src/xyz_agent_context/module/message_bus_module/_work_board_mcp_tools.py
last_verified: 2026-08-07
stub: false
---

# _work_board_mcp_tools.py — 工作板工具,以及模型能写什么的边界

## 为什么与 _message_bus_mcp_tools 分文件、又挂同一个 MCP server

**挂同一个 server**:工作项的作用域是 team **房间**,而房间就是一个 bus
channel —— 能在房间里说话的 agent,恰好就是该维护这块板子的 agent。做成独立
Module 要新端口、新 instance 生命周期,还得反过来去查 bus 的表(跨 module
依赖,铁律 #3)。

**分文件**:板子有自己的状态机和自己的「平台/模型」写入边界,混进 500 行的
消息工具里会把这条边界埋掉。

## 这条边界就是本文件的重点

模型可以 open / claim / finish。模型**不能**写:

- `stalled` —— 平台从 `bus_agent_activity` + 差事超时推导(铁律 #15)
- `paused` —— 停止留下的。**能暂停自己板子的 agent 就能关掉巡查**,而巡查正是
  这个功能要加的监督
- `cancelled` —— 用户的决定,不是 agent 的

`work_update_status` 用 `MODEL_SETTABLE` 白名单挡住,并在错误里说明**为什么**
不能写,而不是只说不行。

## `_resolve_team_room` 为什么查 activity 而不是读注入头

`bus_agent_activity` 的镜像行**只有 trigger 的 team 分支会写**,所以「在
team-owned channel 里有一条 running 活动行」恰好就是「存在工作板」的充要条件。
peer DM 或 owner 私聊解析成 `(None, None)`,工具带理由拒绝 —— 好过凭空造一个
team,写下一个永远不会有人看到的工作项。

代价:这依赖 activity 行的写入时机。若将来 team 分支不再写它,这里会静默退化
成「没有板子」(而不是写错板子)——方向是安全的。
