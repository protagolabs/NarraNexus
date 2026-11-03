---
code_file: plugins/builtin.channels.narramessenger/src/narranexus_plugins/narramessenger_module/_narramessenger_service.py
stub: false
last_verified: 2026-11-03
---

## 2026-11-03 — credentials come from a one-time claim, not the guide

**Symptom (prod, v1.21.16).** Binds still failed, now with the new log line: `bind failed at reveal-bearer: flow_status=waiting_connection bearer_word=True`. The session had reached `waiting_connection` (where the bearer used to appear), yet `_BEARER_RE` found nothing.

**Why.** NarraMessenger changed the connected guide: it no longer prints any credential ("This guide never prints the bearer"). Its "Narra CLI Local Handoff" section instead has the agent `POST /bind-agent/claim-credentials?token=<bind_token>` (no body), whose JSON carries `secret_token` (the Narra bearer), `matrix_user_id`, `matrix_access_token` and `homeserver_url`. The claim is accepted **once** per bind link; a retry is accepted for 10 minutes. Probed against the live api.netmind.chat on 2026-11-03 with a real bind link.

**What changed.**
- All regex credential scraping is gone (`_BEARER_RE`, `_MATRIX_ACCESS_TOKEN_RE`, `_HOMESERVER_RE`, `_MATRIX_USER_RE`, `_parse_setup_guide`). The guide is read only for its status (`_flow_status`). `_FLOW_STATUS_RE` reads both layouts: the pre-profile `- **Status**: `waiting_profile`` and the connected `> **Bind Flow Status**: `waiting_connection``.
- `do_bind` drives the state machine by status: not in `_CLAIMABLE_STATUSES` (`waiting_connection`, `connected`) → report-profile → `_await_claimable` (the same bounded re-read as before, now waiting for the status instead of a bearer) → claim → homeserver egress gate → runtime-ready → upsert.
- `_parse_claim` requires all four fields as non-empty strings; anything less fails at step `claim-credentials` before runtime-ready, so the remote side is never completed with a credential we cannot use. What the user is told after a failed claim depends on the status (see `_claim_hint` below).
- The claim response is parsed in memory and **never logged**; the incomplete-claim log names which fields arrived, never their values.
- The homeserver gate now checks the claim's `homeserver_url` (same threat: it comes from the user-chosen link host).
- The wait constants are now `_STATUS_DELAYS` / `_STATUS_DEADLINE_S` (they time a status wait, not a reveal), and the deadline dropped from 14 s to 12 s: the claim adds a round trip inside the cloud HttpStore's 20 s budget, so ~8 s is left for claim + runtime-ready + upsert.
- Failure steps are now `parse-link`, `egress-gate`, `setup-guide`, `report-profile`, `await-connection`, `claim-credentials`, `homeserver-gate`, `runtime-ready`, `connect-gate`, `network` (`reveal-bearer` and `matrix-access-token` are gone). `_guide_shape` no longer reports `bearer_word`.
- `_claim_hint` picks the advice after a failed claim: a 4xx other than 429 means NarraMessenger refused this link, so only a new link helps; a 5xx / 429 / no response / malformed body may be transient, so the user is told to paste the same link again within 10 minutes.
- A runtime-ready failure with a 5xx or no status also carries the retry hint: the claim is already used, and re-pasting the same link inside the window recovers. A 4xx there, and a refused homeserver, are not things a retry fixes, so they stay as they were.
- **Recovery path.** If runtime-ready landed remotely but our side failed (HttpStore timeout, upsert error), a re-paste finds the guide at `connected`: report-profile and the wait are skipped and the claim is retried inside its 10-minute window. That is why `connected` is in `_CLAIMABLE_STATUSES`.
- The success envelope's `connection_mode` now says `matrix` (it said `gateway`, left over from the Gateway era); the MCP setup text says "Direct Matrix transport" for the same reason.

**Gotcha.** Re-pasting an already-claimed link into a *different* agent within the 10-minute window hands that agent the same Matrix identity. This is NarraMessenger's retry design (the old guide-scraping had the same exposure); the link is the owner's own one-time secret.

**Gotcha.** Do not claim before `waiting_connection`: the claim is one-time, and burning it on a session that then fails leaves the user only the 10-minute retry window. The status wait exists to make sure the claim happens once, at the right moment.

## 2026-11-02 — wait for the bearer after report-profile; every failure is logged

**Symptom (prod).** Every NarraMessenger bind after 10-30 failed with "Profile reported, but the runtime credential isn't revealed yet". The response size (213 bytes) was the only clue: that branch, like most `do_bind` failures, logged nothing.

**Why.** The bearer appears in the setup guide once the bind session reaches `waiting_connection`. `do_bind` POSTed report-profile and re-read the guide **once, immediately** (the whole bind took 128–239 ms), so a guide still rendering the previous state turned into a failed bind. `_await_bearer` now re-reads it with growing delays (`_REVEAL_DELAYS`) and stops as soon as the bearer shows up; a guide that already has the bearer skips the wait entirely. The wait gives up at `_REVEAL_DEADLINE_S` (14 s measured from the start of `do_bind`, fetches included, leaving ~6 s for runtime-ready and the upsert; it logs at info why it ended — each re-read runs under `wait_for` with the remaining time, which is safe because a read has no side effects): the cloud `narra_bind` MCP tool reaches this through `HttpStore` (`module_system/data_access/channel_store.py`) with a 20 s HTTP budget, and a wait that outlived it would report a bind that actually succeeded as a timeout. It bounds the wait only: runtime-ready still runs under the session's 25 s timeout, so a late bearer followed by a slow runtime-ready can still pass 20 s (rare; the retry finds the bearer at once and completes). runtime-ready is deliberately not cut short either — cancelling it mid-request can leave the remote side bound and ours not. When the time left is no longer than the next delay the wait ends instead of sleeping for nothing. Do NOT put a timeout around all of `do_bind` instead — cancelling between runtime-ready and the upsert leaves the remote side bound and ours not. An empty re-read (404/5xx) keeps the last guide that did arrive, so the status the user is told is not lost. runtime-ready is still only called once the bearer is in hand, so a session that never releases it is never completed remotely and nothing is persisted.

**What the user sees when it still fails.** `_not_revealed_message` names the guide's `Bind Flow Status` (parsed by `_FLOW_STATUS_RE`, anchored to the rendered `**Bind Flow Status**` label, as a bare identifier — at most 40 `[A-Za-z0-9_]` chars, so upstream free text can never ride along) and tells them to confirm on the NarraMessenger side or generate a new link.

**Logging.** Every failure returns through `_fail(agent_id, step, message, detail)`, which logs the step (`parse-link`, `egress-gate`, `setup-guide`, `report-profile`, `reveal-bearer`, `homeserver-gate`, `runtime-ready`, `connect-gate`, `network`, `matrix-access-token`). When the bearer or the Matrix token is missing, the detail is `_guide_shape(guide)`: length, flow status, whether the word `Bearer` occurs, and the prose section headings. Fenced code blocks (``` and ~~~) are stripped first (their `# comment` lines look like headings and the guide's examples carry the bind token / Matrix token), headings must fit a plain-text alphabet, and anything token-shaped (`syt_`, `://`, `=`, 16+ alphanumerics, 3+ digits — catches a UUID-style token) is dropped — so an upstream format change shows up in the log instead of having to be guessed. A non-OK report-profile that is let through (WRONG_STATE / no code) is logged at info with its status. Anything that can carry caller- or host-controlled text is bounded: the report-profile code at 100 chars, the connect-gate message at 200 with the bind token replaced, and a network failure logs **only the exception class** — aiohttp's messages embed the request URL, which carries the bind token (`Connection timeout to host https://…/<token>/setup-guide.md`).

**Timeouts.** `aiohttp.ClientTimeout` raises `asyncio.TimeoutError`, which is not an `aiohttp.ClientError`; it used to escape `do_bind` as a 500. It now maps to the same "network error reaching NarraMessenger" envelope.

## 2026-10-29 — bind session pinned in cloud

`do_bind`'s session now uses [[safe_http]] `egress_connector()`: in cloud the
connector's resolver only returns validated public addresses, so the gate's
answer and the dial can no longer differ (DNS rebinding). `trust_env` is now
`not is_cloud_mode()` — a `*_PROXY` would resolve the name itself and bypass
the pin; desktop keeps system-proxy support. A connect-time `UnsafeUrlError`
returns the same "not a public NarraMessenger host" message as the pre-check.
Stored URLs used after bind (matrix homeserver, backend_base_url in the
trigger / send paths) are NOT re-gated here — separate follow-up.

## 2026-10-28（二）— `_is_allowed_target` 改调共享包装；3xx 记日志

- PR#418 review I1：`_is_allowed_target` 不再自己判 `is_cloud_mode()`，改调
  `url_safety.assert_public_http_url_in_cloud`（部署感知只在 [[url_safety]] 一处），本层只保留
  「任何异常 → 记日志 + 返回 False」的约定；local 下包装直接放行，行为不变。
- review M6：`_get_text` 遇到 3xx（重定向一律不跟）时 `logger.warning` 记**状态码**，否则调用方把
  空串映射成「Bind token not found or expired」，上游一旦加 301 跳转，排查方向会被带偏。
  **只记状态码，绝不记 / 回显 `Location`**（上游可控内容，等于把反射面从 body 挪到 header）。
  测试 `test_refused_redirect_is_logged_by_status_without_location`。

## 2026-10-28 — bind 时的出网门（SSRF 整改）

`do_bind` 有两个服务端抓取目标都受攻击者影响：用户粘贴的 bind 链接的 host（正则只限定
`https://<host>`，`https://quota-api/...` 照样匹配），以及从该 host 返回的 setup-guide 里
**爬出来**的 homeserver URL。云端现在：

- 链接 base 在**开任何 session 之前**过共享 `assert_public_http_url`（`_is_allowed_target`，
  任何判定失败 = 拒，fail closed）；被拒回固定文案，不回显解析结果。
- 解析出的 homeserver 在 `runtime-ready` **之前**过同一个门：被拒则远端 bind 不会被完成、
  凭据也不落库。guide 里没有 homeserver 时维持原有下游处理。
- `_get_text` / `_post_json` 都 `allow_redirects=False`：host 门之后再跟 302 就等于没门。
  实测（2026-10-28）api.netmind.chat 的 setup-guide / bind-agent 端点直接回 404/500，不走
  重定向，所以这条不影响正常 bind。
- 本地 / 桌面跳过门（单可信用户，与 MCP 先例一致）。
- **步骤失败不再回显上游文本**：以前 `report-profile failed: <error>` / `runtime-ready failed: <error>`
  把 bind host 返回 JSON 里的 `error` / `message` 原样拼给用户——host 是用户选的，等于反射任意
  上游内容。现在用户文本只由 `_step_failure` 生成：`<step> failed (HTTP <code>[, <已知码>])`，
  只有 `_KNOWN_ERROR_CODES`（目前仅 `WRONG_STATE`，NarraMessenger 文档化新码时再加）原样展示，
  其余原文进日志。`_post_json` 仍返回原始 `error` 供控制流用（`WRONG_STATE` = 已过 profile 步）。

**后续项（本 PR 刻意不做）**：

- worker 侧加载已存 homeserver 时再门一次——`matrix_trigger.MatrixTrigger` 的 connect / 媒体
  下载、`_matrix_send` 的上传 / 发送 / 编辑 / 撤回、MCP 工具与 module 的发送路径都直接用存储的
  `matrix_homeserver_url`（connect 时一次 DNS 很便宜；send 路径需评估每次的开销）。
- `backend_base_url` 同样是存储后被服务端带 bearer 去打的目标：`narra_cli_client`（取
  `cred.backend_base_url` 作为 narra CLI 端点）和 `matrix_trigger` 的 authorize-event 闸
  （用它 + `bearer_token` 发请求）都不复查。bind 时它来自已过门的链接 base，但同样能被下面的
  直写路径改掉。
- bind 不是唯一写入口：`PUT` / `PATCH /api/agents/{id}/channels/{channel}/credential`
  （`backend/routes/agents/channel_credentials.py`）可以直接写 `matrix_homeserver_url` /
  `backend_base_url`，绕过 bind 门。要么在这条写路径对这两个 URL 字段过门，要么在加载时复查。

## 2026-08-19 — `runtime-ready` now carries `platform="nexus"`

NarraMessenger labels an agent's origin platform from the `runtime-ready`
call; their field **defaults to `"default"` when the key is absent**, which
is what every agent we ever bound looked like on their side (reported by
宋顺君 2026-08-19: "新绑定的 platform 显示 default，而不是 nexus"). The call
previously posted `None` as its body, so the tag was never sent.

`PLATFORM_TAG = "nexus"` is now posted as `{"platform": PLATFORM_TAG}`.

**Why runtime-ready and not report-profile** (the question that was actually
asked): report-profile is CONDITIONAL — `do_bind` skips it whenever the guide
already revealed the bearer (re-binds, resumed sessions), so a tag sent there
would silently go missing for exactly the flows that re-run most often.
`runtime-ready` is the single call every bind path makes, and it is the step
whose semantics match the label ("this agent is now usable") — which is also
what the NarraMessenger side asked for.

The value is a module constant rather than a literal so the test can assert
against the same symbol; it is deliberately NOT env-configurable — it
identifies the product, not the deployment (local, DMG and cloud all bind as
`nexus`).

Contract risk to watch: the platform silently ignores unknown body keys, so a
rename on their side would fail open (back to `default`) with no error on
our end. The test asserts the exact body of the runtime-ready POST, which
catches OUR side regressing; catching THEIRS requires checking the agent
list in NarraMessenger after a real bind.

## 2026-08-11 — `do_bind` now persists `profileId`

The `runtime-ready` response's `data.profileId` (when present) is now
written to `nexus_profile_id` on the upserted credential — previously it
was read nowhere, so the column stayed `""` for every row despite existing
in the schema since inception. This unblocks the credential manager's new
`get_by_profile_id` reverse lookup (see that file's mirror doc) for the
prewarm endpoint. `rdata.get("profileId", "") or ""` mirrors the existing
`principalId` / `roomId` handling — tolerant of the field being absent
(current prod behaviour) or explicitly `None`.

## 2026-07-02 (Commit 6) — bind flow switched to Direct Matrix

The driver now completes the bind by calling
`POST /bind-agent/runtime-ready?token=<bind_token>` (Direct Matrix) instead
of `POST /api/agent-gateway/connect` (Gateway/Polling). The setup guide's
Runtime Selection Rule is explicit that Direct Matrix is the default path
("Do not ask the creator to choose Matrix vs Gateway"). Gateway is still a
supported transport on the NarraMessenger backend for callers that hit
`/connect` directly with a stored bearer, but this driver no longer takes
that path.

Two new behaviours land with the switch:

1. `_parse_setup_guide` now extracts a fourth field — `matrix_access_token`,
   the `syt_...` string from the guide's Matrix Connection Details table.
   The Matrix HTTP API accepts ONLY this token; the guide is explicit that
   Matrix rejects the Narra bearer with `M_UNKNOWN_TOKEN`. The regex is
   conservative (`syt_` + 20+ alphanumerics) so stray `syt_short` prose
   examples don't shadow the real value.
2. `do_bind` refuses to persist a credential row when the guide reveals
   the bearer but not the Matrix access token. Reason: MatrixTrigger.connect
   raises ValueError on empty access_token, which the base treats as a
   permanent failure and disables the credential. Returning a clean error
   from `do_bind` gives the owner a better recovery hint ("re-copy the bind
   link and try again") than a silent auto-disable of a half-provisioned
   row.

The response's `roomId` is stored as `bind_room_id` on the credential — the
room where the owner will send the first test message per the guide's
Post-Bind Routing Verification step.

## Why it exists

The deterministic bind-flow driver, shared by the `narra_bind` MCP tool and the
`/api/narramessenger/bind` backend route. It replaces the original fragile path
(tell the agent to read `setup-guide.md` and self-bind) — where the agent could
pick Direct/Gateway on its own and often failed to persist the credential to our
DB. Here WE always pick Direct Matrix and always write the row.

## Design decisions

- **Single input = a pasted bind command/link.** `_parse_bind_command` extracts
  the `<token>` from a `.../<token>/setup-guide.md` URL (and derives the base URL
  from the host), or falls back to the last token-looking word for a looser
  `narra bind <token>` paste, defaulting the base to `api.netmind.chat`.
- **Flow** (current, 2026-11-03): GET setup-guide → if its status is not yet
  `waiting_connection`, `report-profile` (the agent's name+bio from the `agents`
  table, truncated to 30/200) → re-GET until `waiting_connection` (or
  `connected`, a re-paste after a bind that landed) → one-time
  `claim-credentials` → `runtime-ready` → `upsert` the credential row.
- **Identity from the runtime-ready response** — `matrixUserId` /
  `principalId` / `roomId` come back from runtime-ready (authoritative; the
  claim's `matrix_user_id` is the fallback); the credentials come from the claim.
  **These three ids describe the AGENT's own Matrix identity, not the
  binder's** — no bind call returns who ran the bind flow. `roomId` is
  stored as `bind_room_id` and is the only trace of "where the bind
  happened"; it's what `NarramessengerTrigger._maybe_claim_owner` later
  matches against to auto-claim an owner (see that trigger's mirror doc).

## Gotchas

- **The status label is the only thing still read from the markdown**
  (`_FLOW_STATUS_RE`) — if NarraMessenger renames it, binds fail at
  `await-connection` and the log's `_guide_shape` shows the new layout.
- **The bind state machine may require human confirmation** on the
  NarraMessenger side before `waiting_connection`. If so, `do_bind` returns an
  error naming the status and asking the owner to confirm there and re-paste.
- WRONG_STATE from `report-profile` is treated as "already past the profile step"
  and ignored; the status wait still decides next (an unclaimable status fails
  at `await-connection` and never reaches the claim).
- **`do_bind` never writes `owner_matrix_user_id`/`owner_name`** (X2/X3 root
  cause, fixed 2026-07-02) — don't "fix" this here by trying to scrape an
  owner identity out of the setup-guide markdown; the guide describes the
  AGENT's binding, not the binder. Owner resolution happens downstream, on
  first inbound DM in the bind room (`NarramessengerTrigger._maybe_claim_owner`).
