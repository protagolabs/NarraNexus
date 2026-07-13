---
code_file: src/xyz_agent_context/utils/npm_cli.py
last_verified: 2026-07-13
stub: false
---

# npm_cli.py — generic resolver for npm-global CLI binaries

## Why it exists

A login shell has npm's global bin dir + the node bin on `PATH`; a process
spawned by a Docker `CMD`, a GUI launcher (launchd), or our MCP runner
frequently does NOT — its `PATH` is stripped to something like
`/usr/bin:/bin`. That makes both the `<cli>` binary **and** its
`#!/usr/bin/env node` shebang invisible, so the spawn fails with ENOENT even
though the CLI runs fine in the user's terminal. This is exactly the class of
bug lark-cli hit in issue #53.

The fix strategy (find node/npm → their bin dir is where `npm install -g` drops
symlinks → prepend that to the child's `PATH`) already lived inside
[[lark_cli_client]] as private `_resolve_lark_cli` / `_discover_node_bin_dirs`.
This file **generalises** it so a new npm-global CLI (OfficeCLI) can reuse the
same strategy without cross-importing lark's Module (binding rule #3).

## Upstream / Downstream

- **Used by:** [[officecli_client]] (`resolve_npm_cli("officecli", "OFFICECLI_BIN")`).
- **Depends on:** stdlib only (`glob`, `os`, `shutil`, `pathlib`).

## Design decisions

**Resolve node/npm to locate every npm-global bin.** The dir that holds
`node` / `npm` is exactly where `npm install -g` drops its bin symlinks (true
for vanilla, Homebrew, nvm, and n), so resolving those two tools also locates
any npm-global CLI and satisfies its `env node` shebang. Static fallbacks
(`/usr/local/bin`, `/opt/homebrew/bin`, `~/.npm-global/bin`, …) plus
version-manager globs (`~/.nvm/.../bin`, `/usr/local/n/.../bin`) cover hosts
where even node/npm are off this process's `PATH`.

**Stateless (unlike lark's memoised version).** The discovery cost is a handful
of `os.path.isdir` calls, negligible next to spawning a subprocess — so this
one recomputes each call rather than caching. A useful side effect: a user who
installs the CLI mid-session gets it re-discovered on the next call.

**Resolution order** in `resolve_npm_cli`: explicit `env_override` env var
(absolute + executable) → current `PATH` → an augmented `PATH` from
`discover_node_bin_dirs()`. When nothing resolves it returns the bare `name` so
the caller still gets a clear ENOENT rather than a confusing failure.

## Gotchas

- lark keeps its **own private copy** of this logic for now — this util is for
  **new** callers. Don't rip lark's copy out expecting them to share; that's a
  separate follow-up.
- Returns `(executable, extra_path_dirs)`: the caller must prepend
  `extra_path_dirs` to the child process's `PATH`, not just use the executable —
  otherwise the `env node` shebang still can't find `node`.
