---
code_file: src/xyz_agent_context/repository/team_work_repository.py
last_verified: 2026-08-10
stub: false
---

# team_work_repository.py — 工作板的数据访问

## 为什么在 repository/ 而不在 module 里

项目约定 repository 一律放 `repository/`;更实际的原因是**它有两个不属于
module 的消费者**:`MessageBusTrigger` 的巡查候选查询,和取消端点的
停止→暂停联动(`backend/routes/runs.py`)。放进 module 会让这两处反向依赖一个
module 的私有实现。

## 设计决策

- **`list_active` / `teams_with_active_work` 是巡查的成本护栏**。「板子空了就
  一个 run 都不产生」这条保证,落在这两个查询排除 `paused` 与终态上。
  `teams_with_active_work` 一条查询覆盖整个 fleet —— 与 `_agents_with_pending`
  同一形状、同一理由:否则每轮要问每个 team 一遍才知道它没事干。
- **`pause_by_root("")` 是 no-op,不是 match-all**。前置列时期的工作项
  `root_run_id` 为 NULL,把空值当「同一棵树」会让一次停止冻结整块板子。
  测试专门钉了这条。
- **未知 id 返回 False/None,不抛**。item_id 来自模型写的工具调用,一个拼写
  错误必须读作「没找到」,而不是变成 agent 转述给用户的「平台故障」。
- `pause_by_root` 逐行写并吞掉单行失败:部分暂停的板子仍然好过一块继续复活
  工作的板子。

## 上下游

- **上游**:[[_work_board_mcp_tools]](agent 侧)、`backend/routes/runs.py`
  (停止→暂停)、未来的巡查候选查询
- **数据**:`team_work_items`,见 [[schema_registry]]

## 2026-08-10 — `list_visible`: why the user's board is a repository query

The board endpoint originally hand-wrote its own `SELECT` in `routes/teams.py`,
because it needs one state more than `list_active` does (`paused`). That split
the feature's SQL across two layers, and the project's rule that new raw SQL
owes a real-MySQL test then applied to a statement sitting in a route — where
nobody would think to look for it.

So the variant lives here instead. The pair reads as one decision:

  * `list_active` — the AGENT's board. Hides `paused`, which is what makes a
    stop actually stop: patrol asks this question, and a parked item must not
    read as "unfinished, go chase it".
  * `list_visible` — the USER's board. Shows `paused`, because deciding whether
    to resume is the user's call. If the UI reused `list_active`, pressing stop
    would erase the task from the board and a stop would be indistinguishable
    from a delete — exactly what the pause-not-cancel decision exists to avoid.

Both are covered against a real MySQL (`test_team_work_repository_mysql.py`):
they share the generated-placeholder `IN (...)` shape, which is the shape that
has produced a 1064 in this codebase before.
