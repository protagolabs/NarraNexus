---
code_file: src/xyz_agent_context/bundle/importer.py
last_verified: 2026-08-18
stub: false
---

## 2026-08-17 — SEC-07：manifest 里的 `skill_name` 也是不可信输入

skills 段原先自己拼 `skill_archives_dir / f"{skill_name}.zip"` 和
`_full.zip` 两处。这个 `skill_name` 来自**导入的 bundle manifest**，也就是
谁做的 `.nxbundle` 谁写的——和一个表单字段同级的不可信度。同一个文件里
1328 行给 skills 目录做了 `sanitize_filename`，唯独归档这两处漏了。

两处改走 [[skill_backup.py]] 的 `prepare_archive_target()`，`skill_archives_dir`
局部变量随之删除。恶意 manifest 名现在抛 `ValueError`，被 skills 循环既
有的 `except Exception` 记成**单个 skill 的 install failure**（进
`skill_install_failures` / warnings），不会中断整份 import——和其他
per-skill 失败一致。

### 同批修掉的既有缺陷：zip 分支的归档行从来没写进去过

zip 分支把已经就位的 `tgt` 交给 `backup_after_api_install`，而那个 helper
是为"把外面的 zip 拷进登记处"设计的——它自己又算一次
`archive_target`，得到同一个路径，`shutil.copy2(tgt, tgt)` 必抛
`SameFileError`，被它那个宽 `except Exception` 吞成一条 warning，于是
`register_archive` 永远不执行。**导入含 zip 方式 skill 的 bundle 后，该
用户的 `skill_archives` 里没有对应行。**

后果不是"导出少一个 skill"（前端在没有归档行时默认走 full_copy，改动前后
都一样），而是**导入来的 skill 永远无法以 zip 方式再导出，只能 full_copy
——包更大，且 full 模式会把 secrets 一起带走**。

现在 zip 分支直接 `register_archive(archive_path=str(tgt))`，形状照抄
full_copy 分支。回归测试 `test_imported_zip_skill_registers_archive` 钉住这
条，改回旧写法会红。

**sha256 一律由落盘文件现算，不取 manifest 的值**（2026-08-17 二审修正）。
初版写的是"优先用 manifest 的，因为包内 zip 与落盘文件逐字节相同"——这句
有两个反例：

1. **de-dup 哨兵**：[[builder.py]] 对同一个 skill 的第 2..N 条 entry 写的是
   `sha256: "shared"`（它们共用一个 `archive_ref`）。importer 对每条 entry
   都 upsert 一次、后写覆盖先写，于是"两个 agent 共用一个 zip skill"的
   bundle 导入后，`skill_archives.sha256` 就是字面量 `"shared"`。`or` 只兜
   `None` / 空串，兜不住哨兵。
2. **`tgt` 已存在时不拷贝**：上面那句 `if not tgt.exists()` 意味着用户此前
   自己传过同名归档时保留旧字节，而 manifest 的 digest 描述的是新 zip。

这一列的唯一用途就是完整性，写进一个已知有时为假的值比多算一次 hash 差得
多。`test_shared_skill_import_records_a_real_sha` 用**两个 agent**的 bundle
钉住它（单 agent 复现不出来），断言 `re.fullmatch(r"[0-9a-f]{64}", ...)`，
并先断言 manifest 里确实带了 `"shared"` 哨兵——否则用例会因为错误的理由变
绿。

> `full_copy` 分支的 `s.get("sha256", "imported")` 是同一类哨兵，但不在本次
> 改动面上，要改单独一条 commit + 自己的用例。

> 这个缺陷藏了这么久的直接原因是 `skill_backup.py` 那个宽
> `except Exception`（铁律"不要为了日志干净吞异常"）。收窄它是紧跟其后的
> 独立 commit，不和本条混在一起，否则说不清哪个修复对应哪条测试。

## 2026-08-11 — bundle 导入 MCP URL 加 SSRF 筛（安全审计 P0-3）

`mcp_urls` 写入路径（hint→`_ins`）此前**不做任何 URL 校验**，绕过了 create/update 路由的
`_blocks_internal_url`——一个普通用户上传含内网 MCP URL 的 `.nxbundle` 就能把
`http://169.254.169.254/...` 之类种进库，随后被 agent 运行时 fetch。现在写库前跑同一道
**cloud-only** DNS-free 筛（`is_obviously_non_public_url`，**parse-safe**：畸形/非串 URL 判为不安全而非抛异常——裸 `urlparse` 会因坏 IPv6/非串炸出去、经 `_rollback_partial_import` 把整份 import 回滚）：命中即 skip 该行、warning 带 host、
其余 import 照常。local/桌面不筛（localhost MCP 合法）。

## 2026-08-10 — 工作板还原、id 重映射与回滚

board 随 team 行一起写入,三处 id 各有各的处理:

- `assignee_id` 经 `id_map` 重映射;**落在导出闭包之外的 assignee 变成未认领**
  —— 这是真的、且可行动的状态,好过一个没人能追的悬空引用。
- `root_run_id` 恒为 NULL(导出侧已丢弃,理由见 [[builder]])。
- `channel_id` **在 bus channel 还原之后回填**:team 行写在 channel 之前。
  bundle 没带聊天时保持为空 —— 板子在 UI 上照样完整可用(那个视图按 team_id
  查),只有巡查要等房间建起来,而巡查本来也需要一个房间才能说话。

回滚清单同步加了 `team_work_items`,否则失败的导入会留下一块孤儿板子。

## 2026-07-23 — 导入修剪超长 agent_name / agent_description(#71)

confirm 写 `agents` 行前,用 `_clamp_agent_text` 把 name/description 截到
`AGENT_TEXT_MAX_LENGTH`。**顺序关键:先 dedupe 再截 `final_name`**——`dedupe_name`
撞名时追加 ` (n)` 后缀且没有长度预算,若先截到 255 再 dedupe,`"…255… (1)"` 会
变成 259 又越界(review #1)。所以对 dedupe 结果再 clamp 一次,`final_name` 才是
真正入库的值;`agent_name` 无 UNIQUE 约束,截后偶发同名无害(等同手动建同名)。
根因:raw `db.insert` 绕过 Agent 模型的 max_length,超长值入库后每次编辑/删除
反序列化都炸 string_too_long(insertable-but-unreadable)。被截的 agent 记进
`written_summary["agent_fields_trimmed"]`(`[{agent_name, fields}]`)并各加一条
`warnings`——修剪只发生在 `confirm`(_confirm_inner 写库阶段),`preflight` 不改
数据,所以是 **confirm 返回的 summary** 里能看到哪些 agent 被截(前端 done 屏
现在也列出 warnings 正文,见 [[BundleImportPage.tsx]])。
测试:tests/bundle/test_agent_field_length.py(含重复导入的后缀越界回归)。

## 2026-07-13 — skill install to the known skill_dir

The full_copy + zip skill-install branches pass the manifest's `skill_dir` as `install_skill(..., target_dir_name=)`, so a full_copy overwrites `skills/<skill_dir>/` (restoring the credential the workspace snapshot stripped) instead of landing in a temp-derived name. Fixes the arena double-dir bug.

## 2026-07-10 — opt-in IM channel credential import (force-inactive + clash skip)

Imports `agents/<aid>/channel_credentials.json` (opt-in bundles). Two invariants:

1. **Force-inactive**: every credential lands with its active flag = 0
   (lark `is_active`, others `enabled`), regardless of the source value. This is
   the anti-double-connect guard — the user must manually activate in the new env
   (via the channel settings toggle → `POST /api/<ch>/set-active`), which is what
   claims the single connection slot each IM issues per bot.
2. **Clash skip**: a credential whose bot-identity (lark `profile_name`; slack
   `team_id`+`bot_user_id`; telegram/discord `bot_user_id`) is already bound in
   the target env is SKIPPED, not overwritten (stealing a live bot would be
   destructive). `preflight` surfaces these as `credential_clashes`; `confirm`
   enforces the skip and counts it.

`rewrite_row` now EXEMPTS credential tables from the user-attribution
force-overwrite loop: slack/telegram/wechat `owner_user_id` and discord
`owner_user_id`/`user_id` are IM-side ids, NOT NarraNexus user ids — reattributing
them would corrupt the owner-trust signal. `agent_id` is still remapped via
STRUCTURED_ID_FIELDS. Rollback already sweeps every agent_id-bearing table so the
credential rows are covered automatically. Table metadata is the shared
`bundle/channel_credential_tables.py`. Tests: `tests/bundle/test_channel_credentials.py`.

## 2026-06-11 — legacy-bundle tolerance + rollback (v1.3.4 import bug)

A real v1.3.4 bundle failed to import; two independent root causes,
both environment-dependent (see tests/bundle/test_legacy_bundle_import.py
docstring matrix):

1. **Schema drift**: bundle rows carry columns later removed from the
   schema (narratives.embedding_updated_at, unified-memory refactor) —
   fatal on FRESH DBs only, because auto_migrate never drops columns so
   old DBs still accept them. Fix: `_sanitize_for_schema` strips
   unknown columns on every confirm() insert (`_ins` wrapper, 19 call
   sites; preflight bookkeeping insert excluded) and counts them into
   `written_summary["dropped_legacy_columns"]`.
2. **Stringly-typed model reconstruction**: the social-entities branch
   is the ONE importer path that rebuilds a pydantic model instead of
   inserting a raw row (its destination moved to the unified memory
   store); bundle list/dict fields are JSON strings → ValidationError.
   Fix: `_loads_maybe` decodes before construction.

**Atomicity**: confirm() is now a thin wrapper around `_confirm_inner`;
on ANY failure `_rollback_partial_import` sweeps every registered table
carrying agent_id (plus teams/bus by id) for the ids minted in id_map —
no more orphan teams from failed imports. Rollback is best-effort
per-table and never masks the original error. Skill pack FILES are
deliberately not rolled back (shared across agents; re-import
overwrites).

Composite narrative ids (agent_<hex>_<user>_default_N-01): the global
id regex matches the embedded agent id exactly and the same id_map is
applied everywhere, so composites stay internally consistent — pinned
by test.

## 2026-06-09 — import backfills the unified-memory search indexes

`import_bundle` raw-inserts operational rows, which bypasses the live
projection-write points (crud._index_narrative / step_4 interaction /
create_job / send_message), so an imported narrative / job / bus message /
interaction was invisible to `remember` until re-touched. A final pass calls
`backfill_agent_search_indexes` (now in the shared [[backfill]] module — 2026-06-09
it was extracted out of importer so the versioned migration could reuse the exact
same logic) per freshly-imported agent, re-projecting `narratives` /
`instance_jobs` / `bus_messages` / `events` into `memory_<kind>` with the same
searchable text + source_ref each live writer produces. entity is already rebuilt
via `save_entity` during import; observation is LLM-derived (never in a bundle)
and unrecoverable — both out of scope. Best-effort + per-agent isolation,
idempotent. Covers BOTH old bundles (which predate the indexes) and current ones
(same raw-insert path). Scoped to THIS import — `new_agent_ids` are freshly
minted. Counter: `written_summary['search_indexes_backfilled']`. Tests:
`tests/bundle/test_import_backfill.py` + a wiring assert in `test_roundtrip.py`.
(Bulk backfill of a whole existing DB is migration 0001 — see
[[m0001_unified_memory_backfill]].)

## 2026-06-08 — social entities imported via the repo

Social-network import reconstructs `SocialNetworkEntity` objects and writes them through `SocialNetworkRepository.save_entity` (full upsert into `memory_entity`) instead of inserting `instance_social_entities` rows; the row-id rewrite key is `social_entities` and the count comes from the repo. Mirrors the export change in [[builder]].

# importer.py — bundle import pipeline (preflight + confirm)

## 为什么存在

`.nxbundle` 文件 = 跨实例分享 NarraNexus agent / team 的载体。导入端必须同时做完一连串复杂的安全 + 兼容 + ID rewrite 工作，且**必须事务性**（任何一步失败 = 没导入过）。把这些动作集中在一个文件里，让出错路径可控。

## 上下游关系

- **被谁用**：
  - `backend/routes/bundle.py` — 路由层，把上传的 zip 交给 `preflight()`，再用返回 token 交给 `confirm()`
- **依赖谁**：
  - `bundle/security.py` — `extract_zip_safely`、size limits
  - `bundle/id_field_map.py` / `id_schema.py` — ID rewrite 5 层防御 Layer 2 + Layer 1
  - `utils/db/db_factory.get_db_client` — 写库
  - `bundle_preflight_sessions` 表 — token 持久化（B5 修复）

## 设计决策

### Preflight + Confirm 两步走（PRD §8.5）

UX 要求用户先看预览再决定。preflight 解压 + 解析 + 检测冲突，return token；confirm 用 token 真正写库。

### Token 用 SQLite 表存，不用内存 dict

最初实现用 `_PREFLIGHT_STORE` Python 字典，发版重启 / 多 worker 都会丢。**已替换**为 `bundle_preflight_sessions` 表，6h TTL，inline cleanup。

详见 `.mindflow/project/references/scaling_assumptions.md` §1。

### work_dir 在持久路径下

`~/.nexusagent/bundle_preflight/<token>/`，docker compose 可以用 named volume 挂着。

> ⚠️ **SINGLE-WORKER ASSUMPTION**：work_dir 是本机 fs 路径。多 pod 部署 (k8s with ephemeral storage) 时，confirm 命中另一个 pod 会找不到 work_dir。修复方式：mount RWX volume 或上 S3。

### CPU 重活 → asyncio.to_thread

`extract_zip_safely`、`_extract_tar_safely` 都用 `asyncio.to_thread` 包装，避免阻塞 event loop（影响所有用户的 chat WS）。

### ID Rewrite 设计

5 层防御实现了 Layer 1 + 2 + 4：
- Layer 1 = `id_schema.ID_KINDS` regex 字典
- Layer 2 = `id_field_map.STRUCTURED_ID_FIELDS` 表-列登记
- Layer 4 = 自由文本 regex 兜底（`free_text_regex.sub`）

未做的 Layer 3（CI 反向检查）+ Layer 5（roundtrip test）记在议题 6 后续 TODO。

### Unknown module_class 兜底（2026-05-09）

import 时 `module_class` 不在 `MODULE_MAP` 里的 `module_instances` 行**直接丢弃**（不进 DB），并把 `instance_id` 收集进 `skipped_instance_ids`。同 agent 下的子表 (`instance_jobs`, `instance_social_entities`, `instance_rag_store`, `instance_awareness`, `instance_narrative_links`, memory family) 在 insert 前都做这个集合检查 → cascade-skip。一份 `skipped {n} {Class} instance(s) — module class not registered in this build` warning 加到 `summary.warnings`。

为什么这么做：跨机器 import 经常带"源端有但目标端没装"的自定义 Module（比如 MatrixModule）。如果让这些 row 留在 DB 里，runtime 每个 turn 都会 log `Unknown module type, skipping`，而且永远不会被 cascade-delete（除非 agent 整体被删）。

### Artifacts 入包（2026-05-15，bundle_format 1.1）

pre-collect 阶段扫每个 `agents/<aid>/artifacts.json` 把 `artifact_id` 加进 `id_map`（kind=`artifact`，前缀 `art_`）。写库阶段紧接在 `workspace.tar.gz` 解压之后：
- `rewrite_row("instance_artifacts", ...)` 处理 `artifact_id` / `agent_id`
- `file_path`：bundle 里是 workspace-relative，**重新拼上 `{new_aid}_{recipient_user_id}/`** 还原 DB 约定
- `session_id` / `original_session_id` 一律 `None`，`pinned = 1` —— session 跨实例无意义，强制 pin 保证接收方能在 Settings 页面看到
- `created_at` / `updated_at` 走 DB default

### MCP write-through（2026-05-15，bundle_format 1.1）

1.0 时代 `mcp_hints.json` 只是 hint，import 后用户手动重新建 `mcp_urls` 行。1.1 起：
- pre-collect 时扫 `mcp_hints.json` 给 `mcp_id` 分配新 id
- 末尾 `mcp_hints` 段不再只统计数量，而是 `rewrite_row("mcp_urls", row)` 后真插库
- `connection_status` / `last_check_time` / `last_error` 都重置 → 让本机 MCP poller 重新验证
- **gating**：`bundle_format_version < 1.1` 时跳过 write-through 保留 hint-only 老行为（1.0 包是全自动 include 的，import 端硬塞 mcp_urls 会让接收方意外多出一堆来路不明的 MCP）

### instance_jobs 时间戳保留（2026-05-09）

`instance_jobs.created_at` / `updated_at` 在 schema 里**没 DB 默认值**（不像 `module_instances` 有 `default="(datetime('now'))"`）。importer 历史代码沿用其他表的 "pop timestamp → DB 自动填 now()" 套路，结果就 NULL 进库。`JobModel.created_at` 是非 Optional 的 `datetime`，job_trigger 第一次 poll 就在 Pydantic validation 上炸。

修法：从 bundle 原样拷贝 `created_at` / `updated_at`；只有在 bundle 自己缺失时才回填 `now`。同时跑 `tests/bundle/test_roundtrip.py::test_jobs_preserve_timestamps_on_import` 兜底防回归。

## Gotcha

- 重启后 confirm 报 "preflight working dir missing" = 用户中间发版了，让用户重传。
- ID rewrite 在自由文本里**有概率误命中**普通 hex 串（极低，可接受）。
- 整个 confirm 是非事务的（一个 insert 一个 insert），失败时 staging dir 清掉但已经入库的 row 不会回滚。这是 v1 简化，spec 阶段需要包 transaction。

## 2026-08-11 — 导入公告栏到**新** team id

走 [[team_bulletin_transfer]] 的 `write_imported_bulletin`，落在 id map 铸出的新 team id 上。
bundle 是不可信输入：上限重新施加，且无论 payload 声称什么都不写自动总结。

## 2026-08-11 (review) — 回滚也要扫公告栏表

导入失败的回滚里，通用的 agent 表清扫只覆盖**带 `agent_id` 列**的表，
而 `team_bulletin_entries` 只有 `team_id` / `author_id`，两边都不命中。
于是 `write_imported_bulletin` 写进去的行会留在一个下一行就被删掉的 `team_id` 上——
**任何查询路径都读不到**，正是 `_wipe_team_data` 自己论证过的那种孤儿行。
#259 在紧挨着的一行把 `team_work_items` 加了进去，对比之下这个缺口才显出来。
