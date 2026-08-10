---
code_file: src/xyz_agent_context/module/data_access/store.py
stub: false
last_verified: 2026-08-10
---

## 2026-08-10 (PR-3) — general_memory 的 remember / memory_retain 迁入 seam

第二个走 seam 的模块(继 awareness)。两个工具改为
`get_agent_data_store().remember/memory_retain`;DirectStore 复刻原工具体
(MemoryCoordinator/MemoryEngine,**全走 repository 层无裸 SQL——双方言安全**,
这也是它先于 chat 迁移的原因:chat 的 get_chat_history 是 information_schema
+反引号的 MySQL-only 裸 SQL,迁移要先脱裸 SQL),HttpStore 调
`/api/agents/{id}/memory/remember|retain`。backend 路由返回与工具**逐字同
shape** 的 dict,Http 2xx 直接透传 body;非 2xx/不可达降级为工具自身失败 dict
(never 异常)。渲染统一走 [[coordinator]] 的 `format_memory_hits`(唯一真源,
三处 import 同一函数,不再抄拷贝)。
**输入契约共享(真 parity)**:`_clamp_limit`/`_remember_reject`/`_retain_reject`
镜像路由的 pydantic Field 边界(query 1-512、limit 1-100、content/source ≤上限),
**两个 store 都先跑一遍**,所以本地不会在云端会 422 的入参上成功;limit>100 这类
常见越界被 clamp 到 100 而非硬失败。`_send` 收敛为唯一传输层(一个 AsyncClient +
一个 HTTPError 边界,awareness/get/post 共用);`_parse_dict` 把 422 单拎出来给
agent 一条可据以改参的 in-band 消息,不与 401/502 混。
**grep_memory 暂不迁**:HTTP 侧因 ReDoS 拒 regex(PR-2),严格 parity 需先落
timeout-safe regex 引擎(已记 todo),故 grep 仍走 DirectStore 直连——这也意味着
general_memory 的 mcp 容器现仍需 db 凭据,RCE 收益要到 grep 迁完才兑现。


## Why it exists

The data-access seam MCP tools depend on instead of touching repositories/db
directly (blueprint P0). Lets the transport be swapped by the composition root
without changing any tool (rule #9/#20).

## Model

`AgentDataStore` protocol; two impls:
- `DirectStore` — direct repository access, byte-for-byte the pre-abstraction
  behaviour (local `bash run.sh` / DMG own the sqlite db). Db access goes
  through `XYZBaseModule.get_mcp_db_client` — the one loop-aware MCP entry
  point every other tool uses.
- `HttpStore` — calls the backend API forwarding the caller identity headers;
  mcp holds NO db creds (the RCE-remediation goal). Rule #21: HTTP hop, not
  import. Identity gets the call PAST auth (Q6); per-route OWNER checks are
  PR-2's work — do not claim them before they exist.

The interface grows one method per migrated tool (awareness's update is
first; a read method arrives with its first real caller — YAGNI). Both impls
MUST return the SAME strings for the same scenario, and the parity tests
compare the two implementations against one shared fake-backend semantics,
not each against a constant.

## The backend response contract (pre-review C1/C2 — the load-bearing part)

- The agents routes report failure as **HTTP 200 + `{"success": false,
  "error": ...}`** — non-2xx only ever comes from transport/middleware (e.g.
  the Q6 identity 401). An Http method must parse the body; a status-code
  check calls every failure a success.
- The PUT route's convenience default AUTO-CREATES a missing instance (the
  frontend contract). HttpStore opts out via `create_missing=false` so an
  unknown, LLM-supplied agent_id stays an ERROR exactly like DirectStore —
  without that switch the Http path would mint instances for arbitrary ids.
- HTTP-layer failures (401/5xx/unreachable/non-JSON) degrade to in-band
  `"Error: ..."` strings, never exceptions — DirectStore only ever returns
  strings, and a 401 here means the deploy flipped NARRANEXUS_BACKEND_URL
  before provisioning identity keys (ordering contract in factory.py).

## Gotchas

- DirectStore does NOT check the upsert's boolean result — faithful to the
  pre-seam tool. The Http path surfaces the backend's "Failed to update
  awareness". A known, documented asymmetry: Http is strictly more honest,
  Direct is bug-compatible; fixing Direct means changing local behaviour and
  belongs to its own change, not this seam.
