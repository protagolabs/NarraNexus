---
code_file: src/xyz_agent_context/module/common_tools_module/_common_tools_impl/artifact_tool.py
last_verified: 2026-05-14
stub: false
---

# artifact_tool.py — MCP tool registration for create_artifact / upload_artifact_file

## 2026-05-14 — Descriptions rewritten + catch-all error handling

**Descriptions are now the self-contained source of truth.** The old
`description=` strings were vague ("HTML apps, csv tables") and the precise
`kind` enum lived only in the module instruction prose. They now carry the
exact `kind` values, the param contract, the `{artifact_id,version,url}` /
`{error,code}` return shapes, and the explicit "read the error, fix, call
again — a failed call never blocks you" retry contract. They also lead with
a **proactive-use** nudge (artifacts look great, create them directly as
part of the response) and an explicit "pass content INLINE — don't write a
workspace file first and then create_artifact from it" rule (that doubles
the generation cost). The module-instruction block in `[[common_tools_module.py]]`
was trimmed to just judgment guidance and now defers the precise contract
here.

**Catch-all error handling.** Both handlers now also `except Exception` —
an unexpected failure (DB hiccup, disk error) returns a structured
`{error, code: 500}` instead of propagating as an unhandled MCP exception.
Rationale: an opaque tool crash can stall the agent loop; a structured,
retryable error lets the agent read the cause and just call again. The
`ArtifactError` path is unchanged (its messages were already actionable;
`artifact_runner` messages were polished to name valid kinds / drop the
internal `create_text_artifact` name leak).

## 为什么存在

把 `artifact_runner` 里的 filesystem + DB 业务逻辑桥接成 LLM 可用的 MCP 工具。
每次工具调用：获取 DB 连接 → 构建 Repository → 委托 runner → 捕获结构化异常 → 返回 LLM 可读的 dict。

## 为什么在 common_tools_module，而不是独立的 ArtifactModule

Artifact 工具是 LLM 在任一 session 里都可能触发的**能力性**工具，不是某个特定业务场景才有的专属 Module。把它塞进单独 ArtifactModule 只会带来额外的端口分配、进程管理、和 LLM prompt 工程成本——而 common_tools 已有完整的 MCP server 基础设施（factory、timeout 装饰器、backend dispatch 机制），共用是零成本的正确选择。

## 上下游关系

- **被谁用**：`_common_tools_mcp_tools.py` 的 `create_common_tools_mcp_server` 在函数结尾调 `artifact_tool.register(mcp)`
- **依赖谁**：
  - `artifact_runner`（同包 `_common_tools_impl`）——实际的 filesystem 写入、大小/quota/路径检查、DB 编排
  - `ArtifactRepository`（`repository/artifact_repository.py`）——DB 访问层
  - `get_db_client`（`utils/db_factory.py`）——每次调用取 per-loop AsyncDatabaseClient 单例
- **不依赖 `with_mcp_timeout`**：artifact 操作是本地 I/O（写文件 + SQLite/MySQL），不涉及外部网络调用，不需要 timeout 保护

## 注册的工具

### `create_artifact`
- 用途：文本类 artifact（HTML/ECharts JSON/CSV/Markdown）
- 返回：`CreateArtifactToolResult.model_dump(mode="json")` 包含 `artifact_id`、`version`、`url`、`created_at`
- Tool description 说 "Returns a URL the user can already see"——这是故意的 prompt engineering，引导 LLM 不要在回复里再贴一遍 URL

### `upload_artifact_file`
- 用途：二进制 artifact（PNG/JPEG/PDF）从 agent workspace 上传
- 同样返回 `CreateArtifactToolResult.model_dump(mode="json")`
- `local_path` 会在 `artifact_runner.upload_binary_artifact` 里做 realpath escape check

## 错误处理设计

两层 catch：
1. `ArtifactError`（及所有子类：`ArtifactTooLarge`、`ArtifactNotFound`、`ArtifactKindMismatch`、`ArtifactPathEscape`、`ArtifactQuotaExceeded`）→ `{"error": str(e), "code": e.code}`。这些是预期内的结构化拒绝，消息本身已经 actionable。
2. 兜底 `except Exception` → `{"error": "... likely transient — you can call the tool again", "code": 500}`。FastMCP 默认把未处理异常 swallow 成不透明 MCP error，可能 stall agent loop；显式兜底保证**任何**失败都返回结构化、可重试的 dict，LLM 读得懂、能自己重试。

注意 `{error, code}` 这个 shape 前端也消费——`ChatPanel`/`QuotaExceededModal` 靠 `code === 507` 识别配额弹窗，不要改 shape。

## Gotcha / 边界情况

- `get_db_client()` 是异步工厂——每次 tool 调用都 `await get_db_client()`，拿到的是进程级单例，不是新建连接
- Tool handler 内部函数（`create_artifact`、`upload_artifact_file`）是 `register` 作用域内的局部闭包，FastMCP 通过装饰器捕获它们；不要把它们提升为模块级函数，否则会破坏 `register(mcp)` 的封装模式（参考 web_search backend 文件的同一模式）
- `kind` 参数用 `# type: ignore[arg-type]` 是因为 MCP tool schema 只能接受 `str`，而 runner 期望 `ArtifactKind` Literal；runtime validation 在 runner 内部做

## 相关约束

- `artifact_runner.py` 内的业务逻辑文档见 `.mindflow/mirror/src/xyz_agent_context/module/common_tools_module/_common_tools_impl/artifact_runner.py.md`
- MCP server factory 文档见 `.mindflow/mirror/src/xyz_agent_context/module/common_tools_module/_common_tools_mcp_tools.py.md`
