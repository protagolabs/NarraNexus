---
code_file: src/xyz_agent_context/module/office_module/office_module.py
last_verified: 2026-07-13
stub: false
---

# office_module.py — OfficeModule entry point

## Why it exists

Lets the agent create / read / edit `.docx` / `.xlsx` / `.pptx` files via the
OfficeCLI binary and preview them as artifact tabs. A hot-pluggable **capability**
module modelled on [[skill_module]] — always-on, no LLM decision to load.

Deliberately much simpler than the IM Modules (lark/slack/…): **no database, no
per-agent credential or config hydration.** An Office document's entire state is
just files in the agent workspace; there is nothing to persist server-side. That
keeps the module to an entry class + a client wrapper + a security gate + two
MCP tools.

## Upstream / Downstream

- **Registered in:** `MODULE_MAP` via [[__init__]] (module package), and
  `CORE_MCP_MODULES` / `CORE_MODULE_PORTS` on **port 7810** in [[module_runner]]
  (which is the real source of truth for core ports — the CLAUDE.md port table
  is stale/owner-only).
- **MCP tools live in:** [[_office_mcp_tools]] (`office_cli`, `office_render`).
- **Depends on:** OfficeCLI, shipped like lark-cli via npm
  (`@officecli/officecli`) — a self-contained, no-Microsoft-Office-required
  binary.

## Design decisions

**Capability module (always-on).** Like SkillModule, it is a general tool the
LLM may reach for in any session, not a scenario-specific Module. `get_config`
returns `module_type="capability"`.

**Static instructions via `get_instructions` override.** The instruction string
contains literal officecli example syntax (paths, `--prop title=...`), so the
override returns the string **directly** rather than running it through
`str.format` — avoids `str.format` choking on / mangling any braces in the
examples. The instructions describe the **subdirectory convention** (keep each
document in `office/<name>/doc.ext`, never the workspace root) because that is
what lets [[officecli_client]] render a servable preview.

## Gotchas

- The subdirectory rule is not cosmetic: a document at the workspace root cannot
  be previewed (the public-raw route only serves siblings in multi-file mode).
  [[officecli_client]] `render_preview` enforces this and fails early with
  guidance; the instructions steer the agent to do it right the first time.
- Port 7810 must stay in sync between this class's `self.port` and
  [[module_runner]]'s `CORE_MODULE_PORTS` entry (the classic "port set in two
  places" footgun the runner mirror warns about).
