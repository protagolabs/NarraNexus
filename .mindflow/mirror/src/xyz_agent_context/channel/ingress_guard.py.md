---
code_file: src/xyz_agent_context/channel/ingress_guard.py
stub: false
last_verified: 2026-08-24
---

# ingress_guard.py — 「这条消息值不值得处理」

## 为什么存在

2026-08-14，Liam × AI Signal 在一个 NarraMessenger DM 里乒乓死循环
**70+ 小时、6.6 万条消息**，全程监控绿灯。

根因不是某一层写错了，而是**入站路径上没有任何一层问过「这条消息值不值得
处理」**。调研确认（2026-08-24）：IM 入站只有**消息身份去重**（id-keyed，
[[channel_dedup_store.py]]）和**突发合并**（[[channel_debounce_merger.py]]），
没有 per-sender / per-chat 频率限制，也没有跨消息重复检测。唯一的内容指纹层
（`ChannelDedupStore` layer 4）被 `CONTENT_DEDUP_WINDOW_SECONDS` 门控，**当时
所有渠道都是 0，整层是死的**。

于是每条消息无条件跑全套管线：narrative 检索/判官 → persona 更新 → agent
loop → 回复决策。当对面是一个损坏的外部 agent 在逐字复读时，每一层单独看
都在正确工作，合起来是永动机。

**修在 ingress 而不是回复层**：回复层有三个各自独立的耗钱面（agent 自主
回复、DM 兜底回复、后台管线），堵一个漏两个；ingress 是唯一一个卡住就全部
止血的位置。

## 与另外两个熔断器的关系

三个熔断器，三个不同的问题，谁也不能替代谁：

| 熔断器 | 问的问题 |
|---|---|
| [[channel_trigger_base.py]] 快速死亡熔断 | 我自己的凭据坏了吗 |
| `agent_framework/loop/circuit_breaker.py` | 我自己的 turn 一直失败吗 |
| **本文件** | **进来的消息值不值得处理** |

退避 + 冷却 + 半开探测的范式三者同款，这是刻意的——一个仓库里不该有三种
「怎么退避」的写法。

## 设计决策

### 为什么 P1 不做 L0 观察层，却仍然敢上硬熔断

设计文档的完整模型是 L0 观察 / L1 降频合并 / L2 短熔断 / L3 递增熔断。
P1 只落 L2/L3。L0 存在的意义是「先观察不误伤」，这里用**进入条件取合取**
来替代：窗口内必须**同时**满足频率超标**和**重复率超标。

内容各异的正常高频对话——用户连发六条想法、活跃群高峰、job 定时批量——
**永远不可能**满足重复率条件，因此结构性免疫。这不是把 L0 砍掉，是把它的
保护对象换了一种方式覆盖。L1 的合并处理确实推迟到 P2，那是**优化**（省钱），
不是**保护**（止血）。

### 重复率的定义：`1 - distinct/count`

30 条一模一样 → 0.967；30 条各不相同 → 0.0。

写测试时踩过一次：直觉以为「两条正文交替 20 次」的重复率是 0.5，实际是 0.9。
想清楚之后确认公式是对的——**两条台词的乒乓依然是乒乓**，不会因为有两句话
就变得无辜。0.5 那一档对应的是「每句说两遍」，那才是人类会做的事。

### 空指纹算「独一无二」，不算重复

没有正文的消息（无 caption 的文件上传）指纹为空。这类消息**每条都算 distinct**。
反过来会让「连续拖 30 个文件进来」读成逐字复读风暴，而
[[channel_trigger_base.py]] 的空内容闸门早就为无 caption 上传开过同一个口子
（`raw["attachment_refs"]`），这里必须一致。

### 状态分两层存：滑窗在内存，tier 落库

| 数据 | 存哪 | 为什么 |
|---|---|---|
| 滑窗计数 + 指纹环形缓冲 | 纯内存 | 10 分钟就过期的数据，每条入站消息写一行是纯写放大 |
| tier / cooldown_until | 落库，**只在层级变迁时写穿** | 事故跑了 70 小时，期间任何一次重启都会把已经隔离 24 小时的对端重新放行 |

这是本文件最重要的一个决定：**热路径零 DB 写**。每个 session key 在进程生命
周期内只读一次库（首次见到时懒加载），之后全走内存；变迁时写穿。
`test_ingress_breaker_persistence.py::test_only_transitions_are_written` 钉住
这条线——62 条入站消息只允许 1 次写。

注意这与 [[channel_trigger_base.py]] 的凭据熔断器**结论相反**：那个是刻意
纯内存的（它描述的是**活着的** subscriber 状态，停掉的 trigger 不该把隔离
带进下一次 start）。两者不矛盾，因为描述的东西寿命不同。

### 一个时钟，墙钟，可注入

凭据熔断器用 `time.monotonic()` 是对的（纯内存）。一旦要落库，冷却就**必须**
用墙钟表达，而一个状态机里跑两个时钟是「重启差一拍」类 bug 的温床。所以
全程 `utc_now()`，并允许 `now` 参数注入——测试里所有时间断言都是算术，
不睡、不打 fake clock（范式抄 `test_credential_breaker.py` 的 `_armed()`）。

### 冷却表是字面量，不是公式

5min → 30min → 2h → 24h，末位重复。刻意**不用**
`utils/backoff.py::compute_cooldown_seconds`——那个是 `base·2^(n-1)`，
任何底数都凑不出这四个数。这四个数来自设计文档。

### 半开探测保留 tier

冷却到期只放行**一条**探测消息，`tier` **不清零**。理由与
`_breaker_release` 完全相同：一个清完冷却立刻继续复读的会话必须落到
**下一档**，否则持久的循环会永远在最便宜的一档来回震荡。

### tier 会衰减

连续 N 个干净窗口降一级，最终清零。没有这条，一个一年前抖了一分钟的会话
会永远背着一个「能升到 24 小时」的 tier，一年后第一个坏分钟就要付一天。

### fail-open

守卫**不是**授权门。DB 读写失败、guard 自身抛异常，一律放行。对照组是
narramessenger 的 managed authorize hook——那个 fail-closed，因为它**是**
授权门。[[managed_channel_ingress.py]] 的 mirror 要求每个 managed gate 显式
选边，这里选的是 open。

## 上下游

**上游（三个挂载点，同一个 seam `_ingress_admitted`）**：

1. [[channel_trigger_base.py]] `_process_message` —— Slack / Telegram /
   Discord / WeChat + Matrix 的回复路径（这四家 override 了但都调 `super()`）
2. [[lark_trigger.py]] `_process_message` —— Lark **不调 `super()`**，独立挂
3. [[matrix_trigger.py]] 的 `group_silent` 分支 —— 在 `super()` **之前** return，
   但仍然跑记忆管线
4. [[managed_channel_ingress.py]] `before_run` —— Manyfold 托管路径完全绕开
   原生 chokepoint

**没有单一 chokepoint 是本次接线的核心事实**。调研一开始以为只有 Lark 是
例外，`test_ingress_guard_all_paths.py` 一跑就抓出 Telegram / WeChat / Matrix
也各自 override 了 `_process_message`（只是都调了 `super()`）。这类
「N 份手抄」正是 [[channel_trigger_base.py]] mirror 里
`build_trigger_extra_data` 那条教训的同一个缺陷类，答案也一样：
**一个 base seam + 一个 grep 级守卫测试**。

**下游**：`ChannelIngressBreakerRepository`（落库）、
[[channel_audit_events.py]] 的三个事件常量、
`background_llm_alerts.alert_ingress_breaker_tripped`（owner 通知）。

## 坑

- **`content_fingerprint` 是无条件的纯函数**，
  `ChannelTriggerBase._content_fingerprint` 保留 `CONTENT_DEDUP_WINDOW_SECONDS`
  门控后再委托给它。哈希口径必须只有一份，否则去重层和熔断层会对「同一条
  消息」产生两种定义；但那个门控管的是另一个问题（平台是否用新 id 重投），
  不能一起解开。
- **守卫插在 unbound / echo / empty 三道闸门之后**。回声是 agent 自己发的，
  空消息是解析不出来的——把它们计进对端频率，等于让 agent 自己熔断自己。
- **每次 drop 必须留 audit 行**（`ingress_dropped_breaker`，逐条写）。
  「机器人怎么六小时不说话了」必须能从 DB 回答；静默 return 正是让原事故
  跑了 70 小时没人发现的那类盲区。
- **`open_session_count()` / `cooling_session_count()` 是常驻状态**，进
  `health_snapshot()` 和心跳。事故教训 #4：熔断不能只有 trip 那一行。
