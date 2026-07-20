---
code_file: src/xyz_agent_context/module/narramessenger_module/_narra_guide.py
stub: false
last_verified: 2026-07-20
---

## Why it exists

Backs the ``narra_guide`` MCP tool. narra-cli has **no bundled skill
packs** (unlike lark's 27) — its whole agent-facing documentation is ONE
markdown served by the Narra backend at
``{backend_base_url}/api/agent-guide/narra-runtime.md``. Rather than
vendor a copy that goes stale as narra-cli updates, we fetch it live so
the agent always reads the latest; narra maintains the doc, we change
nothing when the CLI grows.

This is the narra analog of ``lark_skill`` + ``_lark_skill_loader``, but
one live document instead of a bundled, link-rewritten pack tree.

## Design decisions (the four guardrails from the design doc)

1. **URL derived from ``backend_base_url``**, never hardcoded test/prod —
   so the guide always matches the transport the agent is bound to
   (``api-test`` binding reads the test guide, prod reads prod).
2. **In-process cache + TTL (600s), keyed by base_url.** The fetch is an
   inline dependency inside an agent turn; re-fetching every call would
   add latency and load the endpoint. ``_now`` is a monotonic-clock seam
   (patched in tests).
3. **Fallback chain on failure**: stale-but-real cached copy → bundled
   snapshot (``resources/narra-runtime.md``) → a minimal built-in string.
   A stale live copy is preferred over the vendored snapshot (it is
   closer to truth). The agent also always has ``narra-cli <domain>
   --help`` as an offline authority.
4. Version-tracking of the local *binary* is handled in run.sh / Docker
   (install latest), NOT here — keeping doc and binary in step is a
   deployment concern.

## Upstream / downstream

- **Called by**: ``_narramessenger_mcp_tools.narra_guide`` (passes the
  credential's ``backend_base_url``).
- **Fetches**: the Narra backend agent-guide endpoint (aiohttp;
  ``_http_get`` is the test seam).
- **Reads**: ``resources/narra-runtime.md`` (the vendored snapshot,
  present in the editable install / Docker source tree).

## Gotchas

- The snapshot is a *fallback*, not the source of truth — it may lag; the
  live copy always wins when reachable. Keep it short (command surface
  only) so drift is cheap.
- ``resources/narra-runtime.md`` ships because deployment uses editable
  installs (``uv pip install -e .``) — the source tree is present at
  runtime. If a non-editable wheel is ever built, add the file to
  package-data or the snapshot fallback silently degrades to the built-in
  string.
