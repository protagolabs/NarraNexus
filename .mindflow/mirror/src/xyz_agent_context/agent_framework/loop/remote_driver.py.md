---
code_file: src/xyz_agent_context/agent_framework/loop/remote_driver.py
stub: false
last_verified: 2026-08-22
---

## 2026-08-22 — steering 不声明:活 channel 过不了 HTTP

live-steering(PR #351)是**进程内**能力:orchestrator 起可 steer 的 run 时把
`SteerChannel` 登进本进程的 `RunRegistry`,producer 与目标 run 同进程,push 直达
loop 的 drain(见 [[run_registry.py]] / [[steer_channel.py]])。远程 driver 这条路
**刻意不参与**,三件事写死:

- **(a) 不声明 steering 是有意的**,不是遗漏:`capabilities()` 仍返回空集
  (2026-07-27 条目定的空协商缝)。远程 run 没有本进程可推的活句柄——run 在
  executor 容器里,`SteerChannel` 在这边,中间隔着一次 HTTP POST + NDJSON 单向
  下行流。声明能力却无处投递,只会让上层误判"这条 run 可 steer"。
- **(b) steering 绝不能进 `build_agent_loop_request` 的 body 白名单**:那个 body 是
  白名单式快照(2026-08-07 / 2026-07-31 条目),只放能序列化过网络的标量/dict。
  `SteerChannel` 是一个活的 `asyncio.Queue` 句柄,绑在本进程的事件循环上
  (线程亲和,见 steer_channel push 契约)——它**没有** wire 表示,塞进 body 也只是
  一个死引用。任何"把 steer 也透传过去"的改法都是类型错误伪装成功能。
- **(c) 真要云端可 steer 需要独立改动**:得给 executor 开一个**上行** steer 端点
  (POST 一条注入到运行中的容器 run),容器内 driver 把它喂进自己的
  `QueueSteeringInlet`——即 steer_channel 里写的"子进程/远程:driver 起 pump 抽干
  channel、写下 runner 的 steer 传输"。这是一条独立的双向协议扩展,不是本 driver
  顺手能带的透传。在它落地前,远程路径对 steering 零知识是**正确**状态。

## 2026-08-19 — 把 origin_declaration 传给 build_agent_loop_request

`agent_loop` 构造 body 时新增 `origin_declaration=kwargs.get("origin_declaration") or ""`，与 `expressive_tools`/`turn_profile` 同形。缺这一跳 = §6 来源声明到不了云端执行器。

## 2026-08-10 (review 修正) — 字段改名 `extra_readable_roots` → `extra_accessible_roots`

纯改名，语义不变：这份授予同时管写与删（confinement 层检查 `file_path` 与 shell 路径），
旧名名不副实。详见 [[policy.py]]。

## 2026-08-07 — 云端路径透传 `extra_readable_roots`

`build_agent_loop_request` 的 body 是**白名单**，不透传任意 kwarg——漏了这一行就会变成
「本地能读共享目录、云端读不到」的两模式分裂（铁律 #7）。见 [[executor_protocol.py]]。

## 2026-08-06 — voice fast mode: TurnProfile 管道（缺省=现状）

kwargs 里的 turn_profile 以 model_dump() dict 进 wire body（白名单式 body，漏键=云端静默丢失，故显式传）。

## 2026-07-31 — 回复契约:投递面由平台声明(expressive seam)

`build_agent_loop_request` 调用新增 `agent_id` / `expressive_tools` 转发
(kwargs 透传)——投递面是 per-run 状态,必须显式过网络边界,否则云端
NexusPower 退回 mute。

## 2026-07-29 — 不再转发 resume_session_id(T6)

`build_agent_loop_request` 调用里去掉该参数。协议字段本身也已删除,见
[[executor_protocol]]。remote 路径不受影响:历史随 `messages` 过去,adapter 在
executor 容器内自己写 transcript。


## 2026-07-28 — resume 能力 HMAC：本文件**无需改动**（记录为什么）

[[executor_protocol.py]] 给 `resume_session_id` 加了 per-call HMAC token（同日
条目）。本 driver 一行都没改，原因值得写下来：它对 wire format **零知识**——
只调 `build_agent_loop_request` 再把返回的 dict POST 出去，所以 token 和
`issued_at` 自动随体走。**请保持这个性质**：任何在本模块里手工拼 body 的改法，
都会让下一个 body 字段的安全属性绕过 protocol 层（token 会漏、canonical string
会对不上，而症状只是"resume 静默失效"，极难查）。

## 2026-07-28 — 请求体转发 `resume_session_id`（resume 化 R2）

`agent_loop` 把 `kwargs.get("resume_session_id")` 放进 executor 请求体
（[[executor_protocol.py]] `build_agent_loop_request` 新参），executor 侧由
[[executor_service.py]] 再传给容器内 driver。与 disallowed_tools 逐字同型的
纯透传；云端容器重建后 session 文件丢失只导致冷启动（working_path/文件缺失
自然回落），功能无损。测试：tests/agent_runtime/test_resume_protocol_threading.py。

## 2026-07-27 — 取消检查统一走 CancellationView（codex v2 死代码修复）

轮询式取消检查改为 `CancellationView(cancellation).requested()`。对
claude/cli_sdk/remote 是等价替换；对 codex v2 是 bug 修复——原
`getattr(cancellation, "is_set", lambda: False)()` 对真实
CancellationToken 恒 False（token 只有 is_cancelled property），进程内
codex turn 此前根本无法被打断。测试
`tests/agent_framework/test_cancellation_view.py` 含该回归用例。


## 2026-07-27 — driver 表面一致化：capabilities() 空协商缝 + 签名整形

三个 driver（claude / codex v1+v2 / remote）统一新增 `capabilities() ->
set[str]`（全部返回空集 = 今天的行为；词汇表见 driver.py 注释，只在能力
真正实现的同一变更里声明）。`streaming` 全员改 keyword-only（所有调用点
本就关键字传参，零行为变化）。codex v2 的 `del kwargs` 改为显式 WARNING
（此前 `disallowed_tools` 被静默丢弃——调用方以为约束生效了）。契约测试
`tests/agent_framework/test_driver_contract.py` 钉住整个表面。

## 2026-07-24 — 请求体转发 `disallowed_tools`（setup-residency B++）

`agent_loop` 把 `kwargs.get("disallowed_tools")` 放进 executor 请求体
（[[executor_protocol.py]] `build_agent_loop_request` 新字段），executor 侧由
[[executor_service.py]] 再传给容器内 driver。纯透传；语义见
[[channel_module_base]] setup-residency。

## 2026-07-22 — 连接失败（建连 + mid-run 掉线）→ 类型化 ExecutorUnreachableError

`agent_loop` 的 `session.post` 块外包一层
`except (aiohttp.ClientConnectionError, aiohttp.ClientPayloadError)` →
`raise ExecutorUnreachableError(...) from e`（[[executor_errors.py]]）。覆盖两种：
- **建连失败**（容器没起/连不上）：ClientConnectorError / ClientOSError。
- **mid-run 掉线**（容器跑一半被杀/网络断）：ServerDisconnectedError /
  ClientPayloadError。

> PR #133 review 指出：初版只 catch `ClientConnectorError`（仅建连），但 docstring
> 声称覆盖 "mid-run drops"——文档与代码口径不一致，mid-run 掉线仍会被兜底编造回复
> 掩盖。已扩到上述基类，口径对齐。

**刻意不 catch**（照旧透传）：`_decode_event` 对 `{"error":...}` 帧抛的 RuntimeError
（executor 转发的**用户 LLM 错误**）、`_MAX_STREAM_BYTES` 自抛的 RuntimeError——两者
都是 RuntimeError 非 aiohttp ClientError；以及 `raise_for_status` 的
ClientResponseError（executor 可达但返 5xx）。上层 [[step_3_agent_loop.py]] 据类名
surface 成 `infra_transient`、skip 兜底、写审计（issue ② 根因）。

## 2026-07-15 — MCP 管道改名 `mcp_urls`/`mcp_server_urls` → `mcp_servers`

值类型从 url 字符串升级为 spec 对象 `{"url": str, "headers": {str:str}?}`，
支撑用户 MCP 自定义请求头（Authorization 等）贯穿全链路。本文件仅机械跟随
改名/类型，职责不变。

## 2026-07-09 (P0 fix) — read stream via `iter_any()`, not line iterator

The original code used `async for raw_line in resp.content`, which
calls aiohttp's `StreamReader.readuntil` under the hood. That helper
raises `LineTooLong` once its buffer crosses `_high_water = limit * 2
= 131072` bytes without seeing a newline. A single NDJSON event line
from the executor carrying a base64 image runs 150-400 KiB (Read
tool's `tool_call_output_item` embeds the image bytes *twice* — once
in `message.content` and once in `toolUseResult` metadata — so even
CLI-downsampled images blow past the 128 KiB ceiling). Result of the
old code path: every multimodal turn on the cloud died silently at
transport, the `async with` unwound the connection, executor observed
disconnect and killed the agent from outside, and the step-3 fallback
covered it up by feeding the pre-crash reasoning to a helper LLM
which invented a plausible-looking reply. The user saw the reply and
believed the agent had read the image; the agent had not (see
"多模态大文件读取事故" root-cause writeup 2026-07-08).

Fix:

1. Read with `resp.content.iter_any()` — that iterator yields whatever
   bytes the transport has, with no per-line ceiling of its own.
2. Manually accumulate an in-memory `bytearray`, split on `\n`, and
   yield each complete NDJSON event.
3. Hold an emergency ceiling `_MAX_STREAM_BYTES = 50 MiB` (aligned
   with the SDK's `max_buffer_size` in `adapters.claude.sdk`) so a
   truly malformed stream still fails fast rather than eating memory.
   Experiment 3 in the writeup showed real image event lines top out
   around 365 KiB even for 3.4 MB source images, so 50 MiB is a
   generous belt-and-suspenders bound, not a tight fit.
4. Tolerate a trailing event without `\n` — the executor should end
   NDJSON cleanly but we don't want to lose the last event to a
   missing newline.

The `_FakeContent` shim in `tests/agent_runtime/test_executor_seam.py`
was updated to expose `iter_any()` (the driver no longer touches
`content` as an async iterator directly). Five new regressions
locked in on the same commit:

- `test_remote_driver_handles_event_line_over_128kib` — 200 KiB
  single event line arrives intact.
- `test_remote_driver_reassembles_line_split_across_chunks` — one
  event fragmented across four `iter_any()` yields.
- `test_remote_driver_multiple_events_in_one_chunk` — two full NDJSON
  events in one chunk both yield.
- `test_remote_driver_raises_when_line_exceeds_max_bytes` — a chunk
  without any newline past the ceiling raises fast.
- `test_remote_driver_yields_trailing_line_without_newline` — the
  no-trailing-newline case yields the last event.

Follow-ups filed separately (see writeup §六): SDK upgrade 0.1.43 →
≥0.2.113 for two independent large-output bugs; IM channel
`ErrorMessage` persistence for the zero-feedback case
(`working_source != "chat"` skips the helper-LLM fallback entirely);
post-fix multimodal e2e; and a design discussion about whether huge
payloads belong on the event stream at all.

## Why it exists

`RemoteAgentLoopDriver` — the network transport behind the step-3
`AgentLoopDriver` seam. Same `agent_loop(...)` async-generator contract
as the local claude/codex drivers, but instead of spawning the CLI
in-process it POSTs to the Executor service and streams the raw event
dicts back. This is the mirror of `HttpAgentRuntimeClient`, one layer
down (the control-plane side of binding rule #20's split).

## Selection / behaviour

- Chosen by `get_agent_loop_driver` when `AGENT_EXECUTOR_URL` is set
  (cloud orchestrator). Unset → local in-process driver, so `bash run.sh`
  and the desktop build are unchanged (binding rule #7).
- Ships the scoped provider configs in the request body
  (`executor_protocol.build_agent_loop_request` snapshots them) because
  they normally ride a ContextVar that does not survive the hop.
- Long-run safe (binding rule #14): `aiohttp` timeout `total=None`,
  `sock_read=None` — gaps between events during long tool calls must not
  abort the stream.
- Re-raises on the executor's `{"error": ...}` line so step-3's except
  path captures it exactly as a local-driver exception.

## Gotcha (burned once, 2026-06-17)

`CancellationToken.is_cancelled` is a **bool `@property`, not a method**.
The first draft called it `()` → `TypeError: 'bool' object is not
callable`, which aborted runs at the first event. Read it, do not call
it. Regression test:
`tests/agent_runtime/test_executor_seam.py::test_remote_driver_honours_cancellation_property`.
