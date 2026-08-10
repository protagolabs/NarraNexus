---
code_file: src/xyz_agent_context/module/data_access/store.py
stub: false
last_verified: 2026-08-10
---

## 2026-08-10 (PR-8) — job 读 by_id/semantic/by_keywords 迁入 seam

三个读方法委托给 [[job_module]] 包导出的 `fetch_job_by_id`/`search_jobs_semantic`/
`search_jobs_by_keywords`（[[_job_reads]]，走 JobRepository 方言安全、自兜底不抛），
backend [[jobs]] 孪生路由调同一批 → byte-parity。DirectStore 外层 try 只兜 `_db()`。
HttpStore GET `/jobs/{id}`、POST `/jobs/search-semantic|search-keywords`；失败键 `error`
与 _parse_dict 降级键一致、无需 remap。HttpStore 的 URL 路径段（narrative_id/
event_id/job_id 等 LLM 供给）用 `_seg`(quote safe="") 编码，防 `/`/`..` 改写请求目标
（否则 Direct 报 not found、Http 报 404，parity 缝）。limit 两 store 都 `_clamp_limit`(≤100) 对齐路由
`Field(le=100)`。invalid-status 文案由共享 helper 产出（两路一致）。**输入契约**：`_job_query_reject`(query 1-512)/`_job_keywords_reject`(keywords≥1) 镜像路由 Field，两 store 发车前都跑（否则空/超长 query、空 keywords 本地 success、云端 422 分叉——同 memory/social 的 `_*_reject`）。

## 2026-08-10 (PR-7) — basic_info view_narrative/view_event/switch_narrative 迁入 seam

三个读方法委托给 [[basic_info_module/_narrative_reads]] 的 `fetch_narrative_view`/
`fetch_event_view`/`check_narrative_switch`——这些是**方言安全**（get_one/get/
get_by_ids，无裸 SQL）且自兜底（返回 dict 不抛）的共享函数，backend [[narrative]]
路由调**同一批**函数，故 Direct/Http 逐字相同。DirectStore 外层只包 `_db()` 获取的
try（fetch_* 自身不抛）保住 invariant。HttpStore GET `/narratives/{id}`、
`/events/{id}`、POST `/narratives/{id}/switch`；失败键是 `error`（与 _parse_dict
降级键一致，无需 remap）。**安全副作用**：旧裸 SQL 不校验 agent_id（跨租户读），
迁移后按调用方 agent 归属过滤。

## 2026-08-10 (PR-6) — social create_agent 迁入 seam（社交模块全部迁完）

最后一个 social 工具。**id 归属是 parity 关键**：`new_agent_id` 由**工具**用
`uuid4` 铸造后作为**入参**传进 seam（不再工具/路由各自随机生成），DirectStore 与
create-agent 路由用同一 id provision → 输出逐字相同（否则随机 id 无法 parity）。
DirectStore 解析 creator owner（AgentRepository）→ `provision_new_agent` → 共享
[[social_network_module]] 的 `format_create_agent_success`（含 warnings 上浮，
incident #5，工具旧版本丢了 warnings，现统一上浮）。无 owner 用共享
`CREATE_AGENT_NO_OWNER_MSG`。失败键：DirectStore `message` / 路由 `error`（含
异常统一 `f"Error: {e}"`）→ HttpStore `_social_write_message` 逆映射。DirectStore
不抛（invariant）。路由 body 加 `new_agent_id`，**用 `pattern=^agent_[0-9a-f]{12}$`
约束**——该 id 会成为 workspace 路径段（base/{user_id}/{agent_id}），无约束的
`../victim/agent` 会跨租户写入；[[provision]] 的 `provision_new_agent` 再做一次
`_SAFE_AGENT_ID` 兜底（唯一 seam 覆盖所有调用方，铁律 #5）。

## 2026-08-10 (PR-5) — social 读 search/contact/stats 迁入 seam

3 个读工具走 seam。DirectStore 复用 `_social_module`（try/except 不抛），调
`search_network`/`recall_entity_info`/`get_agent_stats`。**结果整形共享**：
contact 用 [[social_network_module]] 的 `format_contact_result`、stats 用
`format_stats_result`（DirectStore + 新路由同源，杜绝漂移）；search 原样透传。
HttpStore 调**新建的 POST 孪生路由** `/social-network/{recall,contact,stats}`
（POST 避开 GET `/{user_id}` 路径参数冲突；路由直接返回工具 shape=message 键、
不 normalize，故 HttpStore 2xx 原样透传，`_social_write_message` 只兜自身传输降级）。
**输入契约**：`_social_search_reject`（search_keyword 1-512）、`_clamp_limit`(top_k≤100)、
`_social_id_reject`(contact entity_id) 两 store 都跑。no-instance：search/stats 带
`results:[]`、contact 不带（各按工具 shape）。方法全 repository 无裸 SQL、双方言安全。

## 2026-08-10 (PR-4) — social 写 extract/merge/delete 迁入 seam

第三个模块（3 个写工具）。DirectStore 复刻工具体：`_social_module` 解析
SocialNetworkModule 实例（懒 import 避循环，同工具 `_get_instance_and_module`）
+ 构 temp_module + 调 `extract_and_update_entity_info`/`merge_entities`/
`delete_entity`，失败保工具的 **`message`** 键。HttpStore 调 PR-2 已建的
byte-parity 写路由（`/social-network/{extract,merge,delete-entity}`）。
**唯一 parity 坑=失败键**：路由用 `_normalize_write_result` 把 `message`→`error`
（HTTP 家族约定）；`_social_write_message` 是其**精确逆**（`error`→`message`），
把路由响应 + HttpStore 自身传输降级统一回工具的 `message` 形状。成立前提=三个
写方法**只用 `message` 失败**（实测）。实例缺失文案走 [[social_network_module]]
新增的共享 `social_instance_not_found_msg`（route+DirectStore 同源，杜绝漂移）。
方法全走 repository（SocialNetworkRepository），**无裸 SQL、双方言安全**。
**DirectStore 不抛异常**（管线预审二轮 Important）：`_social_module` 把实例解析
包 try/except、三方法调用处也各有 try/except，本地 db/解析故障返回
`{success:False, message:"Error: ..."}` 而非抛错——否则同一故障 HttpStore 是
message dict、Direct 抛错，parity 破。extract 工具里的 `setup_mcp_llm_context`
**已删**（预审二轮 Important）：它读 `agents` 表 + 会 raise LLMConfigNotConfigured，
而 extract 方法纯 repository 不用 LLM——留着既给 mcp 加 db 依赖又是异常破口。
**输入契约**：`_social_id_reject` 镜像路由的 entity-id `Field(1..128)` 边界，两个
store 发请求/查库前都跑（同 memory 的 `_*_reject` 模式），否则空 id 本地会建空实体
返回 success、云端 422 分叉；**严格按 Field 长度语义、不 strip**（路由 min_length
计字符、接受空白 id，strip 会自造分叉）。社交**读**（search/contact/stats）需另建
孪生路由，另个 PR。

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
