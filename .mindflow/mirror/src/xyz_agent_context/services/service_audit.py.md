---
code_file: src/xyz_agent_context/services/service_audit.py
last_verified: 2026-08-17
stub: false
---

## 2026-08-17 — `_last_heartbeat_at` 初值改为 `-inf`

与 [[channel_trigger_base.py]] 同一个 bug 形状：门禁是
`time.monotonic() - _last_heartbeat_at < heartbeat_interval`，而 monotonic 在
Linux 上从开机计数，所以 `0.0` 读作"在开机那刻发过心跳"，宿主机比 interval
（默认 60s）年轻时第一拍被静默跳过。改成 `float("-inf")`，让"从没发过"成为真正
的哨兵。窗口只有 60 秒、且会自愈，所以影响比 channel trigger 那两处小得多，但
写法不一致本身就是下一个人照抄时的坑。

调用点 `module_poller` / `job_trigger` / `message_bus_trigger` 都不传 `force`，
所以它们的首拍确实走这道门。守卫见
`tests/channel/test_first_cycle_on_a_fresh_host.py`。

# service_audit.py — 长跑后台循环的 L2 可观测性助手（ServiceAuditor）

## 为什么存在

源自事故教训 #4/#5：EC2 上的 JobTrigger / ModulePoller 只有 L1（"进程还活着"）
可观测性，poll 协程一旦卡死，进程看着健康但其实没干活；而应用日志在
`docker restart` 时会丢。本助手让任何长跑循环一次接线，就在 DB 留下黑匣子轨迹
（started / stopped / heartbeat / error）。**心跳过期或缺失**这件事本身就暴露了
`ps` 抓不到的僵尸。

## 一个循环怎么用

init 时构造一个 `ServiceAuditor("<service>")`（很便宜——DB client 首次写入才惰性
获取）。`started(detail)` 一次；每个 poll cycle `heartbeat(detail)`；关闭时
`stopped()`；except 里 `error(str(e))`。

## 为什么 heartbeat 带计数器

`heartbeat()` 有节流（默认 60s），免得 5s 的循环刷爆 DB。detail 里带累计工作计数
（如 `enqueued_total`）——这就是区分"卡死"和"空闲"的关键：新行但计数冻结 = 在空转，
旧行 = 循环彻底停跳。

## 坑

- 所有写入都是 best-effort 且吞异常——**观察者绝不能拖垮被观察者**。丢一条审计行也
  好过卡住一个 poller。
- 持久化在 `repository/service_audit_repository`；表是 `schema_registry` 里的
  `service_audit`（auto-migrate 建）。本助手只是上面的节流 + 生命周期词汇层。
- 从 channel 专属的 `lark_trigger_audit` 泛化而来；新循环应复用本助手，别再为每个
  服务新建一张审计表。
