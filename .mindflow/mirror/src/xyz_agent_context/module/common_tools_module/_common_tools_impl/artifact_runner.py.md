---
code_file: src/xyz_agent_context/module/common_tools_module/_common_tools_impl/artifact_runner.py
last_verified: 2026-05-08-r2
stub: false
---

## 2026-05-08 addition — ArtifactEventBus integration

Both `create_text_artifact` and `upload_binary_artifact` now call
`get_artifact_event_bus().publish(agent_id, event)` just before returning their
`CreateArtifactToolResult`. The event type is `"artifact.created"` for version-1
artifacts and `"artifact.updated"` for iterations (`is_iteration=True`). The
event payload includes `artifact_id`, `version`, `kind`, `title` (truncated to
200 chars), and `session_id`. This wires the runner into the real-time WS fan-out
without adding any coupling to the WS layer — the bus is a one-way fire-and-forget
publish call.

# artifact_runner.py — 文件系统 + 数据库的 artifact 落地层

## 为什么存在

Agent 产出的可视化内容（ECharts JSON、HTML app、CSV 报告、Markdown 文档、图片、PDF）
不能直接存到 DB（太大、不适合 TEXT），也不能只存文件（需要元数据查询、版本追踪、
quota 控制）。

`artifact_runner` 是"两条腿同时落地"的协调层：

1. 写文件到固定目录结构
2. 在 DB 里原子插入 / iterate artifact + version 行

这样 HTTP serve 层可以用文件路径返回 raw 内容，查询层可以用 SQL 做过滤 / 分页 / 统计。

## 上下游关系

- **被谁用**：`_common_tools_mcp_tools.py` 里暴露给 LLM 的 MCP tool（`create_artifact` /
  `upload_artifact_file`）直接调本模块的两个公开函数
- **依赖谁**：
  - `ArtifactRepository` — DB read/write (get_by_id, create, iterate)
  - `settings.base_working_path` — workspace 根目录
  - `Artifact` / `ArtifactKind` / `CreateArtifactToolResult` 来自 schema
- **刻意不依赖谁**：agent_runtime、NarrativeService、任何其他 Module——这是通用工具，不绑场景

## 文件布局约定

```
{settings.base_working_path}/{agent_id}_{user_id}/artifacts/{artifact_id}/v{n}.{ext}
```

- `{agent_id}_{user_id}` 是 workspace root；同 Attachment 的约定保持一致
- `artifacts/` 子目录把 artifact 文件和其他工作文件分隔开
- `v{n}.{ext}` 让 iterate 保留历史版本（v1.md、v2.md 并存）

DB 里存的是相对于 `settings.base_working_path` 的 relpath，
所以 `base_working_path` 换位置只需更新 settings，文件系统搬家后路径解析仍能工作。

## 异常层级（供 MCP wrapper 转换用）

| 异常 | code | 触发场景 |
|---|---|---|
| `ArtifactTooLarge` | 413 | content > 1MB（文字）或文件 > 10MB（二进制）|
| `ArtifactNotFound` | 404 | target_artifact_id 对应的行不存在 |
| `ArtifactKindMismatch` | 400 | iterate 时新 kind ≠ 旧 kind |
| `ArtifactPathEscape` | 400 | upload_binary 时 local_path 不在 agent workspace 里 |
| `ArtifactQuotaExceeded` | 507 | 加上本次 > 500MB per-agent quota |
| `ArtifactError` (base) | 400 | kind 错误（文字函数收到二进制 kind 或反之）|

`.code` 字段让 MCP wrapper 可以无分支地 map 到 HTTP status code。

## 设计决策

### 为什么用 os.path.realpath 做 path-escape 检查

`realpath` 解析所有软链接。如果 local_path 是一个指向 `/etc/passwd` 的符号链接，
`startswith(workspace + os.sep)` 仍然能正确拒绝它。只 `abspath` 不够用。

### 为什么 quota 检查在写文件之前

写文件是不可逆的（就算删 inode，磁盘块要到 GC 才回收）。先检查，通过才写，
避免写了文件但 DB 插入因 quota 失败的不一致状态。

### `now` 固定化 (2026-05-08-r2)

Both `create_text_artifact` and `upload_binary_artifact` previously called
`datetime.now(timezone.utc)` twice — once for the DB row's `created_at`/`updated_at`
and again for `CreateArtifactToolResult.created_at`. This caused a tiny clock skew
between what the DB stores and what the LLM sees in the tool result. A single
`now = datetime.now(timezone.utc)` is now captured at the top of the create branch
and reused in both places.

### 为什么 iterate 时不更新 artifact 的 updated_at

`ArtifactRepository.iterate()` 只更新 `latest_version` 列，保持 `updated_at` 不变。
这是 Task 3 的仓库设计决策，artifact_runner 直接复用，不在这里额外 patch。
如果将来需要追踪 `updated_at`，改 `repo.iterate()` 即可，runner 不用变。

### 为什么文字和二进制用两个独立函数而不是一个

两者的 payload 类型不同（`str` vs 文件路径），size limit 不同（1MB vs 10MB），
path-escape 检查只对二进制有意义。合并成一个函数会造成 Optional 参数激增 + 条件判断。
拆开更清晰，MCP wrapper 的两个 tool 声明也更精确。

## Gotcha / 边界情况

- **`target_artifact_id` 的 kind 校验发生在 quota check 之后**：改变顺序会让用户看到
  "kind mismatch"之前先做了一次额外的 DB 查询（`total_bytes_for_agent`）。
  目前的顺序是：size check → quota check → kind check。
  如果 kind check 更便宜（1 次 get_by_id vs 1 次 aggregate query），可以调整优先级——
  但当前 quota 失败场景远比 kind mismatch 少见，现有顺序 acceptable。

- **DB 事务由 `repo.create` / `repo.iterate` 内部管理**：runner 不需要额外包事务。
  文件已写入、但 DB 插入失败时，orphan 文件会留在磁盘——接受这个 trade-off，
  因为 artifact dir 在下次 create 时会被同 artifact_id 的文件覆盖（iterate 场景），
  或留为无主文件（create 场景，清理脚本可扫描 orphan）。

- **settings.base_working_path 在测试里通过 monkeypatch.setattr 替换**：
  `_relative_to_base` 和 `_workspace_root` 在调用时读 `settings.base_working_path`
  而不是 import 时缓存，所以 monkeypatch 能正确生效。
