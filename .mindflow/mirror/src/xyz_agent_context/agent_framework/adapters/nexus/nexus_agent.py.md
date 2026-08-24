---
code_file: src/xyz_agent_context/agent_framework/adapters/nexus/nexus_agent.py
last_verified: 2026-08-24
stub: false
---

## 2026-08-24 — `_build_request_payload` 写 `options.steerable`

payload 的 options 里加 `steerable = kwargs.get("steering") is not None`——把"这轮可不可控"这个**已有**决定(driver 是否拿到 `SteerChannel`,与 `_open_steer_transport` 决定 stdin 保持打开还是关闭是同一判据)显式带过序列化边界给 runner。runner 每轮都挂 inlet,单靠 inlet 身份判不出可控性;`wait_for_input` 工具的暴露门(见 [[options.py]] / assembly `_steer_channels`)据此 flag 而非 inlet 身份。单一真值来源,别在别处再造第二个。

## 2026-08-24 — capabilities docstring 订正:remote(HTTP)现在**也**带 steering(取代 2026-08-21 节末句)

2026-08-21 节末「remote(HTTP)driver 不声明 steering、remote run 降级成新 turn」**已不成立**。`RemoteAgentLoopDriver` 现按 framework 声明 steering(仅 nexus_power 这类可 drain 的 driver),经 executor `/steer` + `steer_consumed` 帧承载(见 [[remote_driver.py]] / [[executor_service.py]])。故 cloud 上的 nexus_power run **端到端可 steer**,不再降级。本文件只改了 `capabilities()` 的 docstring 措辞(实现 `{event_log, steering}` 不变);契约反转的实体在 remote_driver/executor_service。

## 2026-08-20 — warmup() + _schedule_pool_prewarm()：启动时预填 warm-runner 池

新增 `NexusAgent.warmup()`（executor 启动 lifespan 调用）提前填充 warm-runner 池，
让**进程首个** nexus_power turn 也能拿到已预导入的 runner。此前池只在首个 turn 构造
`NexusAgent`（`__init__`）时才开始填充，首个 turn 的 acquire 赶不上、自己 spawn 冷
runner（dev 实测 ~12s vs warm ~2s）。

`__init__`（本地/桌面每 turn 构造的路径，也是它唯一的预热点——铁律 #7）与 `warmup()`
（executor 启动）**共用一个门控** `_schedule_pool_prewarm()`：`NEXUS_POWER_INPROCESS=1`
或 `pool` 未 enabled 时 no-op；且因 `schedule_refill` 的 `create_task` 需要 running
loop，**无 loop 时静默返回**——所以同步/import 期的 `__init__` 与 async 的 startup 共用
一份判断、都不抛（best-effort 契约，抽方法前 `warmup` 少了 loop 守卫会抛，与 docstring
矛盾）。配套 deploy 侧 `Dockerfile.executor` 的 `compileall`（bake app .pyc）。

## 2026-08-13 — 平台来源绑定：nexus 腿发 identity 头

`_build_request_payload` 在自家网关（`_is_own_gateway_url(base_url)`）且解析到的 slot 配置带
`identity_token` 时，把 `X-NarraNexus-Identity-Token` 并入 `llm_extra["extra_headers"]`
（与既有 `Authorization: Bearer` 共存）。token 取解析到的 slot 配置（anthropic→`claude_config`，
openai→`codex_config`）。off-platform / BYOK 不发。

## 2026-08-13 — profile.expression_nudge 映射

与 include_arg_deltas 同款三态映射：profile 非 None 且字段非 None 才写 options。

## 2026-08-10 (review 修正) — 字段改名 `extra_readable_roots` → `extra_accessible_roots`

纯改名，语义不变：这份授予同时管写与删（confinement 层检查 `file_path` 与 shell 路径），
旧名名不副实。详见 [[policy.py]]。

## 2026-08-07 — options 透传 `extra_readable_roots`

一行透传：把调用方声明的额外可读根放进 TurnOptions payload。适配器不解释其含义
（协作区的概念在平台侧，见 [[step_3_agent_loop.py]]）。

## 2026-08-06 — voice fast mode: TurnProfile 管道（缺省=现状）

_build_request_payload 消费 kwargs["turn_profile"]（模型或 wire dict，单点归一化）：prompt_mode 进 options（无 profile 时 "full" = 旧硬编码值）、reasoning_effort 进 llm_extra（litellm 直通网关）、include_arg_deltas 仅非 None 时覆盖。无 profile 时 payload 语义与改动前一致（test_nexus_turn_profile 钉住）。

## 2026-07-31 — 回复契约:投递面由平台声明(expressive seam)

删除 `_reply_tool_names` 子串猜测(server 名含 "chat" → 猜 reply 工具):
expressive 只读 kwargs(平台经 TurnInput 声明)。改名/换 server 不再静默把
agent 变哑;channel 回复工具(lark_cli 等)也随声明进入表达面。agent_id
同批开始真实传入(旧值恒 "agent")。

## 2026-07-29 — 冷启动路径上钉死 litellm 本地价目表

warm pool 派生 runner 时在**子进程 env** 里 setdefault
`LITELLM_LOCAL_MODEL_COST_MAP=True`。litellm 在 import 时会去 GitHub 拉价目表
(5s 超时后回落到内置副本),这一发请求正好压在我们花 warm pool 换来的冷启动
路径上,还是从加固过的 executor 容器往外打。runner 模块自己也 setdefault 了
一次,但 `-m …runner` 会**先**导入父包——litellm 可能那时已经加载完了,所以
子进程的 environment 才是唯一确定早于一切 import 的落点。
# adapters/nexus/nexus_agent — NexusAgent driver

三件事:遗留签名→TurnRequest(模型配置读同一个 claude_config contextvar——平台 provider 皆 anthropic 协议;bearer_token 补 Authorization 头经 llm_extra 透传)、默认子进程跑 runner(NEXUS_POWER_INPROCESS=1 走进程内,executor/测试用;读行 32MB limit 手动缓冲)、response.done 每路必发恰一次。oauth 凭据显式拒绝(nexus 直驱 API,留在 claude_code)。取消=轮询+SIGTERM 进程组。capabilities={event_log}(声明与实现同批)。


## 2026-07-29 — 温进程池(TTFB 3.4s→1.1s)

_WarmRunnerPool:预 spawn 已完成全部导入(含 litellm 1.8s/215MB)的 runner
闲置待命;acquire 即用即耗(单回合单进程,隔离不变),取用后后台补位;
NEXUS_POWER_POOL_SIZE 定池(默认 1,0 关;每闲置进程 ~350MB RSS,速度换
内存的显式取舍)。driver 构造即预热(首回合与导入重叠)。atexit 收割闲置。

## 2026-08-18 — 透传 origin_declaration

与 claude 适配层同理：只透传，不重新措辞。见 [[sdk.py]] 同日条目。

## 2026-08-21 — live steering:接 SteerChannel

`agent_loop` 从 kwargs 取 `steering`(orchestrator 的 SteerChannel)。**in-process**:用
`QueueSteeringInlet(channel.queue)` 挂到 `serve_turn(steering=inlet)`——loop 直接 drain channel 的
queue,push 即到,无 pump 无拷贝(in-process 与 subprocess 分叉的原因就是这条零拷贝设计)。
**subprocess** 的 steer 传输见下面「(补)」一节(keep stdin open + pump 下 stdin 行、runner 读)。
`capabilities()` 声明 `steering`;remote(HTTP)driver 不声明(活 channel 过不了 HTTP),orchestrator 据
`"steering" in driver.capabilities()` 决定是否让 run 可 steer,remote run 降级成新 turn 而非静默丢注入。见 [[steer_channel.py]]。

## 2026-08-21(补)— subprocess steer 传输

`_run_subprocess`:steer 可能的 run(steer_channel 非 None)**不 close stdin**,起 `_pump_steer_to_stdin`
抽干 channel、把每条写成 `{"steer": …}` 行喂给 runner;回合结束(finally)cancel pump + close stdin。
非 steer 的 run 照旧写完即 close(**零行为变化**)。pump 用 `_CANCEL_POLL_S` 轮询 channel.queue,管道断
(ConnectionReset/BrokenPipe)即退,由 read loop 的 EOF 收尾。

## 2026-08-23(补)— driver 拦截 steer_consumed 行 → SteerChannel.deliver_consumed

`_run_subprocess` 与 `_run_inprocess` 的行读循环都判 `"steer_consumed" in line`:是→`await steer_channel.deliver_consumed(ids)`
(见 [[steer_channel.py]])然后 `continue`,**不**yield 给上层。两条路径都经 `serve_turn`(in-process 也调 serve_turn),
所以拦截点对称。这把「消费证据」在 driver 层(bus 进程、有 DB)交回 producer,绕过 AgentRuntime 的 step 机构——
5 层里最短的正确回路。
