---
code_file: src/xyz_agent_context/module/office_module/_office_impl/officecli_client.py
last_verified: 2026-07-13
stub: false
---

# officecli_client.py — async wrapper for every officecli subprocess call

## Why it exists

Turns the OfficeCLI binary (`@officecli/officecli`) into a single-object async
API for OfficeModule. Modelled on [[lark_cli_client]] but **much simpler**:
OfficeCLI needs no per-agent credential or config hydration — it only reads and
writes files, so this client is stateless.

## Upstream / Downstream

- **Called by:** [[_office_mcp_tools]] — `office_cli` → `run()`, `office_render`
  → `render_preview()`.
- **Depends on:** [[npm_cli]] `resolve_npm_cli` (executable resolution),
  `utils.attachment_storage.get_workspace_path` (workspace CWD), the officecli
  binary.

## Design decisions

**CWD = the agent workspace on every call.** OfficeCLI writes files at
default-relative `./...` paths; running with `cwd = get_workspace_path(agent,
user)` lands those outputs inside the agent's Read-tool sandbox so they are both
readable by the agent and servable as artifacts. Same rationale as lark's
2026-05-28 CWD fix (see [[lark_cli_client]]) — downloads go to CWD, not HOME.

**Executable resolution via [[npm_cli]].** `_exec` resolves `officecli` to an
absolute path and prepends the npm-global + node bin dirs to the child's `PATH`,
so a stripped MCP/GUI/Docker `PATH` still finds the binary **and** its
`env node` shebang. `OFFICECLI_BIN` is the escape-hatch env override.

**`render_preview` rejects office files at the workspace root.** It runs
`officecli view <file> html -o <stem>.preview.html`, writing a **sibling**
preview next to the office file. If the office file sits directly in the
workspace root it fails early with guidance — because the public-raw artifact
route only serves siblings in multi-file mode, so a root-level doc's preview
would 404. Failing early beats registering a broken tab.

**`OFFICE_EXT_TO_KIND`** maps `.docx` / `.xlsx` / `.pptx` → the OOXML
`ArtifactKind`s. Must stay in sync with [[artifact_schema]] `ArtifactKind` and
[[agents_artifacts]] `_KIND_EXTENSIONS`.

**`PREVIEW_SUFFIX` / `preview_name_for` (`slides.pptx` → `slides.preview.html`)**
MUST stay in sync with the frontend [[OfficeRenderer]], which independently
derives the same `<stem>.preview.html` sibling name from the artifact's
`file_path`. Change one, change both.

## Gotchas

- `render_preview` returns the **original office file** as `office_abs` (the
  artifact entry pointer), plus the sibling `preview_rel` — the caller registers
  the office file, not the preview, so "download original" grabs the real doc.
- `officecli view ... html` produces a static, self-contained snapshot (same
  renderer as `watch`, no server); the frontend loads it as a blob. `watch` /
  `mcp` would block until the subprocess timeout, which is why
  [[_office_command_security]] blocks them.
- `_exec` normalises officecli's exit/JSON-envelope handling into a uniform
  `{success, data}` / `{success:false, error}` dict; a `FileNotFoundError`
  (binary missing) returns an actionable "install `@officecli/officecli` or set
  `OFFICECLI_BIN`" message rather than raising.
