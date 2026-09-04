---
code_file: src/xyz_agent_context/channel/credential_mirror.py
last_verified: 2026-09-04
stub: false
---

# channel/credential_mirror.py — dual-write phase

## Intent

Instead of editing six managers with six write vocabularies, `install_manager_mirrors` (run by `module/contributions.register_all`) wraps each manager-backed channel's write methods (`WRITE_METHODS`) so that after the original completes the row is re-read through the manager's read method and upserted (or deleted) in the generic store — one mapping (`to_raw_dict` + the schema) instead of six; a mirror failure is logged, never breaks the bespoke write. `backfill` copies the active rows once (migration m0004); an inactive row reaches the generic table on its next write. Deleted in 4d when reads switch.
