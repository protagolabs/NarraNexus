---
code_file: src/xyz_agent_context/message_bus/team_rooms.py
last_verified: 2026-08-17
stub: false
---

# team_rooms.py — 「团队房在哪」的唯一答案

## 为什么存在

「团队房 = `created_by` 为 `team_<id>` 标记的那个 group 频道」是个**约定**，不是
定律，而它长出了四份独立实现：工作板工具、公告通知、teams 路由，以及最新的 job
origin 解析。四份拷贝意味着约定要改那天有三次漏改的机会——给一个团队开第二个房
间、或者加个 `is_primary` 标记，漏掉哪份，哪份就解析到跟产品其余部分不同的房间。

促成抽取的是：**最新那份落地时就已经漂移了**——它丢掉了兄弟实现的
`bus_agent_activity` 兜底分支，于是同一个团队在工作板工具和 job 面会解析出不同
结果。

## 没有被折进来的那一处

`backend/routes/teams.py` 用一条 `created_by IN (...)` 一次解析**多个**团队的房
间。它看着像第五份拷贝，其实不是：它回答的是另一个问题，用这个 helper 重写会把
一次索引查询变成 N 次——正是 repository 层存在的意义所要避免的 N+1。它共享**约
定**，不共享代码。

## 契约

返回 `None` 而不是空串：三个调用方都拿它当写入目标，半个答案会把行写到 `""`。
房间还没建是正常状态而不是错误（房间是懒创建的）。永不抛——三个调用方对「没有房
间」都是安静地放弃（没有板子 / 不发公告 / 没有 job origin），拿到异常也做不了别的。

## 上下游

被用：[[_work_board_mcp_tools]]（注入身份分支）、[[team_bulletin]]、
[[_job_mcp_tools]]。共享约定但不共享代码：`backend/routes/teams.py`。
