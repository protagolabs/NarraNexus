---
code_file: tauri/src-tauri/src/sidecar/lark_preflight.rs
last_verified: 2026-04-23
---

# lark_preflight.rs — best-effort lark-cli + skill-pack installer on dmg startup

## Intent

Bring the bundled desktop app to parity with `scripts/run.sh`'s `check_deps`
flow for the **optional** Lark/Feishu integration. Iron rule #7 — run.sh and
dmg must run the same logic — applies even to optional integrations; without
this, the dmg silently disables every Lark Module because `lark_skill(...)`
MCP tool returns "not found" and the Agent has no way to know why.

## Why best-effort + detached

- Lark is optional. A user with no node/npm must still be able to use the
  rest of NarraNexus, so every failure path logs a warning and returns.
- `npm install -g` can hang for minutes on slow registries. Blocking setup()
  on it would delay the window and the rest of the services. The preflight is
  fire-and-forget (`tokio::spawn`), capped with `tokio::time::timeout` per
  subcommand (120 s for lark-cli, 180 s for the skill pack).
- First Lark call after a slow install may still fail because the install is
  still in progress — acceptable; user can retry and by then it's done.

## What it mirrors

The entire flow is a port of the lark-install block in `scripts/run.sh`
(roughly lines 82–188 at time of writing). Keep changes to that block in
lockstep with this file:

- `npm install -g @larksuite/cli` when `lark-cli` absent on PATH
- `HOME=$HOME npx skills add larksuite/cli -y -g` when
  `~/.agents/skills/lark-shared/SKILL.md` AND
  `~/.claude/skills/lark-shared/SKILL.md` are both missing

## Upstream / downstream

- **Called by:** `lib.rs::run()` inside `setup()`
- **Calls out to:** system `sh`, `npm`, `npx` binaries — all optional

## Gotchas

- `command_exists` spawns `sh -c "command -v …"`. On Windows this would fail,
  but the dmg target is macOS-only so we don't guard further.
- `npm install -g` with no prefix config may need sudo on some setups.
  run.sh's warning text about `npm config set registry` / permissions /
  network is mirrored here so users see the same remediation hints.
