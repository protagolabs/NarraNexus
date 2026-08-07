---
code_file: src/xyz_agent_context/message_bus/team_files.py
last_verified: 2026-08-07
stub: false
---

# team_files — 枚举团队共享目录

## 为什么存在

共享目录一直有文件，缺的是**问它有什么**的能力。落盘用生成的 file_id 命名，原始名只活在
索引里；索引之前不存在，所以发现渠道只有「某个 agent 在房间里念一遍绝对路径」，别人恰好
注意到。这让「你收到文件了吗」变成模型之间的社交协议——模型记得叙述时才可靠。

## 关键设计

**授权按成员关系，不按 owner。**一个 user 拥有多个 team，「这是我 owner 的 team」**不是**
读它的理由——按 owner 判会让该 owner 的任意 agent 读到他所有 team 的目录。

**空列表是 success，不是 error。**「还没人分享」是一个答案。返回错误会把模型推向重试或为
一个正常工作的工具道歉。拒绝时**不带 `files` 键**，调用方无法把「拒绝」误当成「空目录」。

**每条带绝对 path。**列出来但不能操作等于散文——path 正是 Read 接受的形式，也是 team prompt
一直告诉 agent 用的形式。

**逻辑不放在 MCP 工具模块里**：这样「成员关系而非所有权」这条规则不依赖 MCP 传输即可测试，
同一个函数日后也能直接支撑路由。

## 上下游

- **被谁用**：`bus_list_team_files` MCP 工具（[[_message_bus_mcp_tools.py]]）；team prompt
  （[[message_bus_trigger.py]]）明确指向该工具而非让 agent 自己猜路径
- **依赖谁**：`team_files` 表（由 [[_bus_attachment_impl.py]] 分享时写入）、`team_members`
