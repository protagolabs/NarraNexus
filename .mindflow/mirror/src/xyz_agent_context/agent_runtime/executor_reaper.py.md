---
code_file: src/xyz_agent_context/agent_runtime/executor_reaper.py
stub: false
last_verified: 2026-08-19
---

## 2026-08-19 — 空闲回收加跨进程活性否决(prod 事故修复)

**事故**:2026-07-31 prod,用户在群聊 @ agent 后回复失败,前端报
`infra_transient` / executor unreachable。真因不是网络也不是资源:backend 的
reaper 把**正在干活**的容器停了。

**为什么原来的不变量是假的**:executor 容器按 **user** 共享,但
`AgentAdmissionController` 是 **进程级** 单例,而 reaper 只在 backend 起
(`backend/main.py`)。云端编排跑在 backend + workers 两个进程里:backend 只看得见
自己的网页单聊 run,看不见 workers 里的群聊/消息总线、定时任务、渠道触发。于是
"0 活跃 loop" 实际只意味着"**在问的这个进程里**是 0"。触发组合=用户先在网页单聊
聊过(backend 打了 idle 戳),TTL 到期那一刻正好有个 workers 驱动的 run 在跑。
纯单聊或纯群聊用户都不会踩到——所以它藏了很久。

**修法**:`claim_idle_users(ttl, is_busy=...)` 加一个跨进程否决,数据源是
`events` 表(每个进程都往里写,事故教训 #5:DB 痕迹比日志可靠),判活复用
[[run_recorder.py]] 的 `user_has_live_run` / `run_is_live`(30s 心跳、3 拍判死)。
不需要新表、不需要 Redis。

**为什么否决必须在 `claim_idle_users` 里,而不是在 `reap_once` 里过滤**:claim 是
**破坏性**的——返回名单的同时就把 `_idle_since` 戳删了。若在 reaper 侧过滤,被
跳过的用户戳已经没了,要等本进程下一次 `release()` 才重新打戳;而"主要在 workers
里跑"的用户在 backend 永远等不到那次 release → 容器**永不回收**,从误杀换成泄漏。
放进去之后,被否决的用户保留原戳,下一轮(120s)继续复查,run 一结束就能收。

**残余窗口(已知,已记 todo)**:`events` 行要等 Step 0 建行、RunRecorder
`_bind_run_id` 才翻成 `running`。从 admission `acquire()` 到那一刻之间(数百 ms~
数秒)DB 里还看不到这个 run,理论上仍可能误杀。要彻底关掉得把 admission 账本本身
变成跨进程(`admission.py` 里预留的 Redis seam,铁律 #20)——那牵动并发闸门本身,
风险面大一个量级,单独排期。本次事故不在这个窗口内。

**可观测**:被否决的 run 写一行 `instance_executor_audit` / `cull_skipped_busy`
(带 `run_id`)。这是 L3 指标:每行 = 一次"老代码会杀掉的在途 run"。计数长期
为 0 要去查护栏是不是没跑,而不是默认问题消失了。

**判决与 stop 之间还有一段路,所以停之前再问一次**:`claim_idle_users` 是一次性
把整批候选都否决完的,而 stop 是逐个串行执行的,每个 `docker stop` 还要等 SIGTERM
宽限期。批里第 N 个用户被停时,它的判决可能已经是几分钟前的了——足够一条总线触发
的 run 起来并走到 step 3、拿到那个还热着的容器。所以 `reap_once` 在每次 stop 前
按用户再查一次(一次带索引的读)。跳过的用户此时 idle 戳已被 claim 拿走,所以它
落在"泄漏"方向而不是"误杀"方向——这是有意的取舍。

**去重不是优化,是指标定义的一部分**:被否决的用户保留 idle 戳(见上),所以
它每轮都会被重新提名、重新否决。逐次写行会让行数变成**运行时长的函数** ——
一条合法跑 10 小时的 run(铁律 #14 说这正常)会写出 ~300 行,读起来像几百次
险情。`_CullVeto` 因此按 **run_id** 去重:只在挡住我们的 run **变了**的时候写行。

去重键**不能**按"这一轮有没有出现"来淘汰。用户一旦在本进程活跃起来
(`acquire` 弹掉 idle 戳)就不再是候选,于是"忘掉本轮缺席的人"会把一条**仍然
活着**的阻塞 run 忘掉,过一会儿再为同一条 run 写第二行——网页单聊 + 群聊混用的
用户恰好就是这次事故的触发画像,指标会正好在目标人群上偏高。所以用
`_blocked_by: user -> (run_id, 连续缺席轮数)`,连续缺席 `_FORGET_AFTER` 轮才丢弃,
既不误淘汰也不无界增长。

pass 生命周期用 `pass_()` 上下文管理器暴露在 `BusyCheck` 上,`reap_once` 无条件
调用(没有这个方法就退化成 `nullcontext`)——不要在协调者里 `isinstance` 嗅探
具体实现,那与本文件"纯协调者"的定位矛盾,而且异常路径会漏掉收尾。

**判决函数放在本文件**:`live_run_elsewhere` / `stale_replacement_is_safe` 都在
这里,`broker_client` 只是 transport,拿 `allow_stale_replace` 这个 bool。这跟
reaper 侧的 `is_busy` 注入是同一种形状:决策在编排层,执行在传输层。

**第三个残余:`NARRANEXUS_RUN_RECORDING_DISABLED` 会把护栏一起关掉**。这个开关
只在 `client.py`(**trigger 路径**)被查,而 trigger 路径正是本护栏唯一要保护的
那一类 run:开关一开 → 没有 recorder → `_bind_run_id` 不跑 → events 行停在建表
默认的 `completed` → 判活答"不忙" → 照杀,且审计表里连 `cull_skipped_busy` 都
不会有(根本没判成忙)。比"绑定前窄窗口"严重得多——它覆盖整个 run 生命周期。
处理:`live_run_elsewhere` 开头查 `recording_enabled()`,关着就一律当忙(等于
**回收整体停摆**),并打 warning。这是有意的保守,不要当 bug 修掉。

**同一处不对称的另一半(未修,已记 todo)**:纯 workers 用户(只用群聊/定时任务)
在 backend 的 `_idle_since` 里从来不出现,他们的容器**从来不会被回收**。
不能靠"给 workers 也起一个 reaper"解决——两个进程各按局部账本回收会互相误杀。
正解是让回收的**候选来源**也变成跨进程事实(DB 里的 per-user 最后活跃时间,
或 Redis 化账本)。

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
