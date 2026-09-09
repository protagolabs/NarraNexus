---
code_file: src/narranexus/platform/narrative/_narrative_impl/instance_handler.py
last_verified: 2026-09-09
stub: false
---

## 2026-09-09 — B-16：新增 `handle_completion_no_narrative`（依赖解析不再强制要 narrative）

## 为什么存在

`InstanceHandler` 是 ModuleInstance 生命周期状态机的落脚点：一个 Instance
完成（COMPLETED/FAILED）之后，谁依赖它、依赖是否满足、要不要从 BLOCKED
拉到 ACTIVE，都在这里判定并写库（`module_instances` 状态 + 让对应 Module
的 `on_instance_activated` 钩子跑一次）。独立成一个 impl 文件是因为这段
状态转移逻辑既不是纯数据访问（要跨 instance 查依赖图、要回调 Module 钩子）
也不是编排层该管的细节（[[module_poller]] / hook_manager 只该说「这个
instance 完成了」，不该自己重写一遍依赖判定）。

## 这个文件不做什么

`handle_completion`（既有方法）**不是**通用的依赖解析器——它硬绑定
narrative：依赖方是谁、依赖是否满足，全部通过 `instance_narrative_links`
表按 `narrative_id` 查。一个从没绑定过 narrative 的 instance（比如
`/api/jobs/complex` 建的 job，那条 route 从不传 `narrative_id`）对它来说
**完全不存在**——不是「查到但不满足」，是从一开始就不在候选集合里。

## 上下游关系

- **被谁用**：[[module_poller]] 的 `_process_completed_instance`（Path B，
  唯一真正驱动依赖链前进的调用点）；hook_manager 的实时路径也调用过
  `handle_completion`，但 `narrative` 为空时直接跳过（那条路径的注释自己
  承认「durable guarantee lives in ModulePoller」）。
- **依赖谁**：`InstanceRepository`（读写 `module_instances`）、
  `InstanceNarrativeLinkRepository`（仅 `handle_completion` 用，
  `handle_completion_no_narrative` 完全不碰）、`module_registry`（拿到
  Module 类去调 `on_instance_activated`，激活后钩子——JobModule 的实现见
  [[job_module]]，只是把 `next_run_time`/`status` 拉活，不直接执行）。

## 设计决策

- **`handle_completion_no_narrative` 是新方法，不是改造 `handle_completion`
  兼容 `narrative_id=None`**：两者查询的数据源完全不同（`instance_narrative_links`
  vs `module_instances.dependencies` 直查），硬塞进一个方法会让分支逻辑
  比拆开还难读，且 `handle_completion` 现有调用方（hook_manager 实时路径）
  的行为必须原样保留——它们的失败模式、日志、runtime cache 更新
  （`narrative.active_instances` 那段）只对「确实有 narrative」的场景有意义。
- **依赖满足的判据是「终态」（COMPLETED 或 FAILED），不是「只认 COMPLETED」**：
  照抄 `handle_completion`/`_check_dependencies_from_db` 的既有语义——「是否
  block 在上游失败」是调用方的策略，本方法只回答「这条依赖走完了没有」。
- **按 `self.agent_id` 过滤 BLOCKED 候选**：`get_by_agent(status=BLOCKED)`
  一次性拉本 agent 下所有 BLOCKED instance 再逐个核对 `dependencies` 是否
  含刚完成的那个——依赖 instance_id 天然同批（同一个 `/api/jobs/complex`
  请求），不会跨 agent，跨 agent 的 BLOCKED instance 必须被忽略（测试
  `test_ignores_blocked_instances_belonging_to_a_different_agent` 钉死）。

## Gotcha / 边界情况

- **触发**：`/api/jobs/complex` 建的 job 从未绑定 narrative_id → **症状**：
  依赖链「永不触发」，下游 job 永远 BLOCKED（GitHub #114/#109）→ **根因**：
  唯一现存的依赖解析路径 `handle_completion` 要求实例先被 narrative 链接，
  这类 job 物理上做不到。修复见本方法 + [[module_poller]] 的分支。

## 相关约束

- 铁律 #8（改一处要 sweep 同类）：把这条依赖链修完整还需要
  [[job_repository]] 的 `update_next_run_time_by_instance` 纳入 `BLOCKED`
  状态、[[job_service]]/`instance_sync_service` 让 Job 自己的 `status`
  跟 ModuleInstance 的 `initial_status` 对齐——四处缺一不可，见各自 mirror md。
