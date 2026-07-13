---
code_file: src/xyz_agent_context/module/office_module/_office_impl/_office_mcp_tools.py
last_verified: 2026-07-13
stub: false
---

# _office_mcp_tools.py — the two OfficeModule MCP tools

## Why it exists

Defines the OfficeModule MCP server's two tools. Both receive `agent_id` +
`user_id` injected by the runtime (same mechanism as the common_tools
`register_artifact` tool).

- **`office_cli(command)`** — passthrough: [[_office_command_security]]
  `sanitize_command` gates + splits the string, then [[officecli_client]] `run`
  executes it in the agent workspace. Full create/view/edit power without
  enumerating every subcommand.
- **`office_render(path, ...)`** — renders a sibling HTML preview via
  [[officecli_client]] `render_preview`, then registers the artifact through the
  **shared** [[registration]] service. The entry pointer is the **original**
  office file (kind = an OOXML `ArtifactKind`), so the download menu's "download
  original" grabs the real .docx/.xlsx/.pptx while [[OfficeRenderer]] shows the
  sibling HTML preview.

## Upstream / Downstream

- **Registered by:** [[office_module]] `create_mcp_server`.
- **Depends on:** [[officecli_client]], [[_office_command_security]], and the
  shared [[registration]] service (`from xyz_agent_context.artifact import
  registration`) + `ArtifactRepository` + `get_db_client`. It does **not**
  import another Module's private impl (binding rule #3).

## Design decisions

**office_render goes through the shared registration service, not the
register_artifact MCP tool.** OfficeModule is independent of common_tools; it
reaches artifact registration through the promoted shared layer. Error handling
mirrors [[artifact_tool]]: structured `registration.ArtifactError` →
`{success:false, error, code}`; catch-all `except Exception` → a retryable
`code:500` dict so FastMCP never swallows a failure into an opaque MCP error
that stalls the loop.

## Gotchas

- ⚠️ **FRONTEND COUPLING:** `office_render` is listed in
  `ARTIFACT_TOOL_BASE_NAMES` in [[ChatPanel]]. Because it registers via the
  shared service (NOT the `register_artifact` tool) yet must be discovered live
  in the panel, it returns `artifact_id` at the **top level** of its
  `tool_output` for that discovery. Renaming the tool → update that constant in
  the same change.
- The two tool handlers are closures inside `create_office_mcp_server(port)`
  captured by the FastMCP decorator — don't lift them to module scope (breaks
  the `create_*_mcp_server` encapsulation pattern shared across Modules).
- `kind` from `render_preview` is a bare `str`; `registration.register_artifact`
  wants the `ArtifactKind` literal — `# type: ignore[arg-type]`, runtime
  validation is in [[registration]].
