---
code_file: backend/routes/runs.py
last_verified: 2026-08-07
stub: false
---
## 2026-08-07 (二次) — 留痕覆盖整个被停集合(PR #252 review Important #4)

初版只对**被点击**的那个 run 留痕。一次点击停掉三层树时,房间里只出现一
条通知,另外两个 agent 是凭空安静下去的 —— 正是这个函数自己 docstring 里
写的「其他成员只能猜是跑完了、崩了、还是还在跑」。

`_leave_room_trace` 改收整个 stopped 列表,按 channel 分组,每个房间一条
(`_post_stop_notice`)。同房间多个 agent 合并成一条,三条独立通知本身就是
另一种噪音。

# routes/runs.py — run 级控制面(目前只有:owner 的停止请求)

## 为什么是 run-scoped 而不是 team-scoped

run 可观察性早已是**平台属性**(#219:每个 trigger run 一份 recorder,
每个读侧走同一个观察端点)。停止是它的写侧孪生,所以以 `run_id` 为键,
任何能说出一个 run 的表面都能用它 —— 今天是 team roster,明天可能是
runs dashboard —— 而不是绑死在团队房间上。

## 只记录意图,不执行中断

run 活在另一个进程(workers),HTTP 请求进不去。真正的中断由那边的
[[cancel_watcher]] 读 `events.cancel_requested_at` 完成。

**记录与投递刻意分离**:点击能在一次往返内拿到答复,不管 workers 进程
当时多忙。这正是需求的核心 —— 2026-07-23 事故是 8 分钟的沉默,不是一次
缓慢的停止。前端据此立刻转「正在停止」,终态则从观察流里等。

## 设计决策

- **owner 校验走 `AgentRepository.resolve_owner(agent_id)`,绝不用
  `events.user_id`**。后者存的是 run 的**触发键**:团队房间里那是**发送者**
  (`usr_<uid>` 或转发的 agent_id)。信它就等于让房间里任何跟这个 agent
  说过话的人都能停别人的 agent。测试专门钉了这条
  (`test_triggering_user_is_not_the_owner`)。
- **403 与前端是否画按钮无关**。按钮的可见性只是提示,这里是边界。
- **已终态的 run 不落旗标**,只回 `already_settled=true`。旗标比请求活得久,
  而 watcher 的判据是 `requested >= started_at` —— 往终态行上盖旗标等于给
  这个 agent 的下一个 run 留了个活陷阱。
- **重复点击保留最初的时间戳**。第二次点击若改写时间戳,`requested >=
  started_at` 的裁决就能被一次(比如重启后落下的)迟到点击移动。

## 上下游

- **上游**:`TeamMemberPanel` 的停止按钮 → `api.cancelRun(runId)`
- **下游**:`events.cancel_requested_at` → [[cancel_watcher]](触发 token)
  与 [[run_recorder]] 的 `sweep_stale_runs`(决定终态写 cancelled 还是
  failed)
- 挂载:`backend/main.py`,prefix `/api/runs`

测试:`tests/backend/test_run_cancel_route.py`
