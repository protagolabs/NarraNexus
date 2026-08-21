---
code_file: src/xyz_agent_context/repository/steer_inbox_repository.py
last_verified: 2026-08-21
stub: false
---

# steer_inbox_repository.py — 运行中插话注入的持久 owner

live-steering 的存储层:producer 往 `steer_inbox` append 一条注入(keyed by 不透明
`run_id`——RunRegistry 的运行句柄,本层不解释),transport 在下个 step 边界把某个 run 的
未消费行 drain 进它的 SteeringInlet 并 mark_consumed。落表(非纯内存队列)是 Owner 决策:
崩溃可恢复、可审计、ack 游标有家(`consumed_at`)。

## 三个操作

- `append` 幂等:`(run_id, msg_id)` 唯一,重投递最多注入一次。先查后插处理常见路径,插入竞态
  经 `is_unique_violation`(双方言共用判据)**只吞唯一约束违反**→"就是重复"返 False,其它插入错误
  (真 bug)照样 raise——不会假新行、也不会把连接/schema 错伪装成重复。
- `pull_unconsumed(run_id)`:该 run 的 `consumed_at IS NULL` 行,按 id 升序=到达序(FIFO)。
- `mark_consumed(run_id, up_to_id)`:给 id ≤ 游标且未消费的行盖章,返回条数;scoped 到 run_id
  且只碰未消费——不动别的 run,重复消费是 no-op。id 上限让 drain 只消费它看见的窗口、之后到达的
  留 pending(不静默丢,铁律 #16)。

## 坑

不是 BaseRepository 子类:自增 id、无实体主键、全是 scoped range 查询(同 artifact_event 的理由)。
读仍返 `SteerInjection` 给 provenance(source/sender)带类型。双方言 `%s` 参数化,标识符别加引号。
