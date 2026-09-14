---
code_file: src/narranexus/platform/schema/attachment_schema.py
last_verified: 2026-09-13
stub: false
---

## 2026-09-13（PR#401 review 🟡2）— `file_marker`：Read-tool marker 的唯一渲染函数

用户上传 marker（`Attachment.synthesize_marker`）与总线附件 marker（`_bus_attachment_impl.build_bus_markers`）
原来是两份拷贝且已分叉（总线侧折了空白、用户侧没折，却有 docstring 声称两边同形）。现在两边都调模块级
`file_marker(head, *, name, path, mime, kind=None, transcript=None)`，形状
`[<head>: name="…", path=…, mime=…[, kind=…][, transcript="…"] — use Read tool to view]`。

- 文件名、transcript 是上传者写的 → `inline_literal`（JSON 字面量、不截断）：不能另起一行，也不能在同一行里
  提前闭合 marker 或伪造 `path=` 等字段。
- `path` 是 agent 原样喂给 Read 的句柄 → 原样打印；它由平台用 base 目录 + file_id + 清洗过的后缀
  （`attachment_storage.on_disk_suffix`）拼成，作者够不着。
- `head` / `mime` / `kind` 平台构造（`head` 可能嵌入调用方已编码的标签）。transcript 只在去空白后非空时出现
  （两边口径统一）。IM 渠道与 WS chat 共用的 `markers_from_dicts` 因此同样受益。

## 2026-07-09 — `Attachment.markers_from_dicts` staticmethod added

Single seam for "list of Attachment-shaped dicts → newline-joined Read-tool markers". Replaces the three private copies (`chat_module._synthesize_attachment_markers`, ad-hoc loops in prior WS route drafts and prior channel context builder drafts) with one implementation on the schema. Malformed entries produce a WARNING (`[Attachment.markers_from_dicts] skipping malformed attachment dict: <type>: <msg>`) — silent drops would recreate the "agent claims no file received" incident the 2026-07-09 fix addresses (see [[context_runtime.py]] for the current-turn callsite; [[chat_module.py]] for the history callsite).

# attachment_schema.py

## Why it exists

The Pydantic shape that travels from frontend upload → WebSocket payload →
JSON memory inside `instance_json_format_memory_chat` → marker text in
chat_history. It is the single source of truth for what an "attachment"
means in the system, so the WS schema, ChatModule's hook code,
CommonToolsModule's dynamic instruction, and the React types all agree
on the same field set.

The model intentionally carries only metadata + the `file_id` reference;
binary content lives on disk under the agent workspace via
`narranexus.platform.utils.attachment_storage`.

## Upstream / Downstream

Producers:
- `backend/routes/agents/attachments.py::upload_attachment` builds an
  Attachment after sniffing MIME and storing the bytes
- `frontend/src/components/chat/ChatPanel.tsx` accumulates Attachments
  in `pendingAttachments` state and sends them with the WS payload

Consumers:
- `backend/routes/websocket.py::AgentRunRequest` accepts a list of
  Attachment dicts via `attachments`
- `narranexus_plugins.chat_module.chat_module` persists them on
  the user message in JSON memory and synthesizes natural-language
  markers (with absolute paths) for chat_history
- `narranexus_plugins.common_tools_module.common_tools_module`
  injects the same paths into a system-prompt block for the current turn
- `frontend/src/components/chat/MessageBubble.tsx` renders thumbnails
  for `category=image`, file chips otherwise

## Design decisions

**Marker carries an absolute path, not a tool name.** The agent's
built-in `Read` tool (Anthropic SDK) is multimodal and natively returns
image / PDF / text content blocks — so we point the agent at a path it
can hand straight to Read. No custom MCP tool, no per-Module instance
to manage, no extra port. This collapses what was once an
`AttachmentModule` into a one-line marker.

**Path resolution lives at marker-synthesis time, not upload time.** A
file might be deleted between upload and read, and the marker should
say `<unavailable>` in that case rather than baking in a stale path.
`synthesize_marker(agent_id, user_id)` re-resolves through
`attachment_storage.resolve_attachment_path` every time chat_history is
built.

**Category derivation lives in the schema, not at the call sites.**
`derive_category_from_mime` keeps frontend and backend in lockstep: a
new mime type only needs to be classified once and every layer benefits.

**`transcript` is now actively populated for audio uploads.** Set by
`backend/routes/agents/attachments.py` via
`narranexus.platform.utils.audio_transcription.transcribe_audio` when the
upload's MIME starts with `audio/` AND the user has an OpenAI-protocol
provider configured. `synthesize_marker` checks this field and, when
present, appends `transcript=<text>` to the marker so the agent reads
the spoken content without a separate Read step. `caption` remains a
reserved field — kept on the model so the JSON memory shape doesn't
need a migration when vision-LLM caption synthesis (Phase 2 of vision
support) lands.

## Gotchas

- `FILE_ID_REGEX` is enforced everywhere a file_id crosses a trust
  boundary: upload, raw download, path resolution. Don't relax it
  without revisiting all three.
- `SUPPORTED_IMAGE_MIME_TYPES` is now an informational constant — the
  agent's built-in Read tool decides what it can render. We keep it for
  thumbnail-rendering decisions and future Phase-2 caption synthesis.
- `synthesize_marker` requires `agent_id` and `user_id` because path
  resolution is workspace-scoped. The AI cannot guess these — only the
  ChatModule hook (which has them on `self`) and CommonToolsModule (ditto)
  call it.

## New-joiner traps

- The model accepts `category` as a free string in some serialization
  paths (it's a `str` enum); always go through `AttachmentCategory(value)`
  when constructing programmatically to catch typos.
- Do NOT add fields here that name a specific module (e.g.
  `read_tool_url`, `mcp_endpoint`). The whole point of this redesign is
  that attachments are tool-agnostic — they're just paths the agent's
  built-in primitives can consume.
