---
code_file: tests/utils/test_workspace_paths.py
last_verified: 2026-08-14
stub: false
---

# test_workspace_paths.py — workspace layout compatibility contract

## Why this exists

The workspace helper is shared by provisioning, runtime, artifact, attachment,
and gateway code, so a seemingly local path change can hide an agent's files
across the system. These unit tests pin both the nested current layout and the
legacy flat fallback independently of any HTTP route.

For Manyfold #832, the suite distinguishes naming a path from guaranteeing a
directory. `ensure_agent_workspace` must create the current path, preserve
contents on replay, propagate filesystem failures, and reject ids that are not
single safe path segments before touching disk.

## Legacy-shadowing decision

Materialization resolves existing candidates first. If a populated legacy flat
directory exists, creating an empty nested twin would make the current-layout
resolver choose the empty directory and make the old contents disappear from
every reader. The adoption test therefore asserts both that the legacy path is
returned and that no preferred-layout twin is created.

## Test boundary

This file owns the synchronous path-helper properties. The cross-layer promise
that `POST /manyfold/agents` waits for materialization and agrees with
`GET .../files/roots` belongs to
`tests/backend/test_manyfold_workspace_materialize.py`.
