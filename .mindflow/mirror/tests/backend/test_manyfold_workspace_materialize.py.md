---
code_file: tests/backend/test_manyfold_workspace_materialize.py
last_verified: 2026-08-14
stub: false
---

# test_manyfold_workspace_materialize.py — Manyfold #832 acceptance

Pins the create contract of [[agents.py]]: `POST /manyfold/agents` returns only
once the agent's canonical workspace DIRECTORY exists on disk. The bug it
locks out returned 200 with the `users` / `agents` rows written and nothing on
disk, which surfaced far away — the platform's runner calls
`workspace.ensure(create=false)` and could not start the sandbox.

## Why it drives the HTTP routes, not the helper

The unit-level properties of `ensure_agent_workspace` live in
tests/utils/test_workspace_paths.py. This file exists for the part that only
shows up end to end: **create and roots are two endpoints that must agree**.
`files.router` is mounted alongside `agents.router` so
`test_create_reports_a_workspace_the_roots_endpoint_can_serve` walks the exact
pair the runner walks (create → read the root → ensure). Asserting on the
create response alone would have passed even in the broken version, because
the broken version's `roots` reported a plausible path too.

Setup mirrors [[test_manyfold_diagnostics.py]]: a bare `FastAPI` app with a
middleware that pre-sets `manyfold_authed` (the gateway token middleware is not
under test), `get_db_client` monkeypatched to the in-memory `db_client`
fixture, and `settings.base_working_path` pointed at `tmp_path` — patched on
the settings singleton, so both module-level and lazy readers see it.

## What each case protects

- **first create / roots round trip** — the #832 regression itself.
- **second same-user agent** — the two workspaces are SIBLINGS under one
  per-user root and neither is inside the other. Nesting them would leak one
  agent's files into the other's `files/list`; the test writes a file into the
  first and asserts the second is still empty rather than just comparing paths.
- **replay repair / replay keeps contents** — the update leg materializes too
  (a deleted workspace comes back), and a populated one is left untouched.
  Together they pin "idempotent" as *repair*, not *recreate*.
- **unmaterializable base** — 500, not a false success. Uses a real
  filesystem failure (a regular file where a parent directory is needed →
  `NotADirectoryError`) rather than mocking `mkdir`, so it would still catch
  the failure if the implementation stopped going through the helper.
- **unsafe agent_id** — the id becomes a path segment here, so `../escapee`
  is rejected with 400 AND leaves no rows behind (validation runs before the
  writes, not just before the mkdir).
