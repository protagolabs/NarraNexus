---
code_file: src/xyz_agent_context/agent_runtime/executor_reaper.py
stub: false
last_verified: 2026-08-21
---

## 2026-08-21 — 回收器不再掐掉活在别的进程里的 run（prod 事故修复）

**事故**：2026-07-31 prod 群聊 @ agent 后回复失败，前端 `infra_transient`。
不是网络也不是资源 —— **回收器把正在干活的容器停了**。

**根因**：executor 容器按 **user** 共享，但 [[admission.py]] 是**进程级**单例，
而本 reaper 只在 backend 起（`backend/main.py`）。云端编排跑在 backend +
workers 两个进程里：backend 只看得见自己的网页单聊 run，看不见 workers 里的
群聊/消息总线、定时任务、渠道触发。所以本文件原文件头那句
"only idle executors (zero active loops) are ever reaped" 只在单进程内成立
—— "0 活跃 loop" 实际是"**在问的这个进程里**是 0"。触发组合＝用户先在网页
单聊聊过（backend 打了 idle 戳），TTL 到期那一刻正好有个 workers 驱动的 run
在跑。纯单聊或纯群聊用户都不会踩到，所以它藏了很久。时间线可精确对账：
09:21:15 + 1200s = 09:41:15，回收器 120s 一轮，09:42:02 那轮命中。

**改法**：让"这个用户忙不忙"成为**跨进程可判定的事实**，数据源是 `events`
表（每个进程都往里写 —— 事故教训 #5）。判活复用
[[run_recorder.py]] 的 `run_is_live`（30s 心跳、3 拍判死）。不需要新表、
不需要 Redis。协作者从三个变四个，多出来的那个回答 **IS** the user idle。

- `live_run_elsewhere()` — 本文件对外的判活入口，**永不抛**，任何失败路径
  都答"忙"，用 `UNKNOWN_RUN` 哨兵区分"真有 run 挡着"和"问不出来"。
- `_CullVeto` — 注入给 `claim_idle_users` 的否决器，按 **run id** 去重审计
  行。被否决的用户每轮都会被重新提名、重新否决，逐轮写行会让行数变成
  **run 时长的函数**（一个 10 小时的 agent ≈ 300 行，而铁律 #14 说这是正常
  的）。指标要数的是"救下了几个 run"，所以每个 (user, run) 只记一次。
- `reap_once` **停之前再查一次**：claim 时统一否决过，但停是串行的、每个
  `docker stop` 还要等 SIGTERM 宽限期，轮到第 N 个用户时它的判决可能已经
  几分钟前了，足够一个 bus 触发的 run 起来并跑到 step 3。
- 回退时 `restamp_idle`：claim 是破坏性的，光 `continue` 就会把误杀换成
  **容器永不回收**的泄漏。

**为什么否决必须下沉进 `claim_idle_users` 而不是在这里过滤** —— 见
[[admission.py]] 同日条目，这是本次改动最关键的一处落点判断。

**kill switch 的连带影响**：`NARRANEXUS_RUN_RECORDING_DISABLED` 打开时，
trigger 路径的 run 根本不会翻 running，DB 会把它们全报成空闲。所以那面开关
一开，**整个回收器停摆**（一律答"忙"）并打警告。观测开关不能顺手变成"允许
销毁容器"的授权。

**可观测**：每次否决写一行 `instance_executor_audit / cull_skipped_busy`
（见 [[executor_audit.py]]）。每行 = 一个老代码会当场掐死的在途 run。
## 2026-07-28 — post_reap 钩子随 per-run 会话票一起删除

`post_reap_fn` 这个机制当初只有一个用途：culling 掉闲置 executor 后，顺手
吊销该用户遗留的 free-tier 会话票。per-run 会话票整套已经不存在（改成每用户
一把长期钱包 key，落在 `user_providers` 里），钩子随之失去唯一调用方，按
铁律 #2/#8 一并删掉而不是留着空转。

## 为什么存在

per-user Executor 容器的 idle-cull 协调者。云端每个活跃用户有一个 executor
容器(~1.5G),长期不回收会把内存占满。reaper 周期性地把空闲超过 TTL 的用户
executor 停掉。

## 设计(优雅:单一职责 + 依赖注入)

三个关注点分离,reaper 是纯协调者,不持有任何一方的内部:
- `AgentAdmissionController`([[admission.py]]) — 并发 + 空闲记账(WHO is idle)。
- `ExecutorReaper`(本文件) — **WHEN** to cull(周期 + TTL)。
- `broker_client.stop_executor` — **HOW**(docker 传输,DELETE /executors/{user})。

reaper 通过构造注入 `controller` + `stop_fn`,可用 fake 完整单测,无需真 broker/
真 sleep(`reap_once()` 是可测的单趟)。

## 坑 / 决策

- **铁律 #14**:只回收空闲(0 活跃 loop)的 executor,绝不碰运行中的 loop。
  `claim_idle_users` 在锁内原子地"认领并移除",避免重复回收。
- **竞态**:认领后、停止前若有新 run 到达并复用了那个容器 → 极小窗口内 run 可能
  连到被停容器;`broker.ensure` 幂等会冷启动一个新的,最坏只是一次冷启动(唤醒
  UX 覆盖)。20 分钟 TTL 下碰撞概率极低。
- **stop 失败**:记录并跳过该用户,不中断整趟;broker 自带的 label-based reaper
  兜底清孤儿。
- **fire-and-forget**:`maybe_start_executor_reaper` 起的后台 task 挂了 done-callback
  上报异常(事故教训 #2:裸 create_task 是地雷)。
- **门控**:`maybe_start_executor_reaper` 仅在配置了 `BROKER_URL`(云端)时启动;
  本地/桌面无 per-user executor,返回 None。在 `backend/main.py` lifespan 启动/取消。
- TTL/间隔:`EXECUTOR_IDLE_TTL_SEC`(默认 1200=20min)、`EXECUTOR_REAP_INTERVAL_SEC`
  (默认 120)。
- **免费额度网关票孤儿回收(2026-07-23)**:新增可选 `post_reap_fn(user_id)` 钩子,
  在成功停掉某用户 executor **之后**触发。用途:回收该用户遗留的 gateway 会话票
  (agent 硬崩溃、`agent_loop` 的 finally 没跑到 → 票没作废)。**为什么此刻安全**:
  reaper 只回收 `claim_idle_users` 认领的空闲用户(0 活跃 loop),所以此刻该用户没有
  在跑的 run,任何 ACTIVE 票必是孤儿 → 直接作废不违反铁律 #14(不需要定时器、不需要
  猜哪个 run 还活着)。stop **失败**时**不**触发钩子(容器可能还活着,票不能动)。
  `maybe_start_executor_reaper` 仅在配了 `SYSTEM_DEFAULT_LLM_GATEWAY_URL` 时装配该
  钩子,钩子内 `GatewayKeyService.from_env(db).revoke_all_for_user`。见
  [[gateway_key_service]]。
