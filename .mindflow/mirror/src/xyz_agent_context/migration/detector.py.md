---
code_file: src/xyz_agent_context/migration/detector.py
stub: false
last_verified: 2026-07-21
---

## Why it exists

Framework detection for the migration Scanner. Given the user's local
filesystem, decides which of Claude Code / Hermes / OpenClaw / Codex a directory
looks like (or `custom`), with a confidence. Detect-only — reads no secrets,
writes nothing.

## Design decisions

- **Signal table (`_SIGNALS`)**, not hard-coded ifs: each framework declares its
  `home_dirs` (standard `~/.<name>` locations, incl. OpenClaw's legacy
  `.clawdbot`/`.moltbot`), `strong` files (high confidence), `weak` files
  (medium). Adding a framework = one table entry.
- `classify_path` scores an explicit dir against all frameworks and returns the
  best; falls back to `custom` (low) when nothing matches — the Custom-Importer
  heuristic + LLM fallback handles that downstream.
- `detect_all` probes the standard home locations and returns the best hit per
  framework, so a machine with several frameworks yields several detections.

## Gotcha

- `AGENTS.md` is a Codex strong signal AND a generic custom hint, so a bare
  `AGENTS.md` dir classifies as Codex (medium), not custom — intentional.
