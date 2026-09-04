---
code_file: src/xyz_agent_context/channel/credential_legacy.py
last_verified: 2026-09-04
stub: false
---

# channel/credential_legacy.py — the retired per-channel tables, described once

## Intent

Batch 4d switched every builtin channel manager onto `channel_credentials`. The six historical tables (`channel_{telegram,slack,discord,wechat,narramessenger}_credentials`, `lark_credentials`) are retired but never dropped (rule #6), and two consumers still need to understand their rows: the one-shot copy migration (`m0004`, re-run as `m0005`) and the bundle importer when it meets a bundle exported before the switch. `LEGACY_TABLES` is the single description of that shape — table name, channel, the enabled column (`is_active` for lark, `enabled` elsewhere), the base64 `*_encoded` / `_encrypted` secret columns and how they decode into the generic values — so the two consumers cannot drift.

## Design decisions

- **Decode at the boundary.** Legacy rows stored secrets base64-encoded; the generic store encrypts. `LegacyTable.to_values(row)` returns plain values (plus the active flag) (lark keeps its base64 `app_secret_encoded`, the SDK's working form) and lets the store encrypt.
- **Copy, never move.** `copy_legacy_tables` inserts only rows whose (channel, agent) is absent from the generic table — the switch is idempotent and a generic row that was already edited wins. A missing legacy table (fresh install) copies zero rows.
- **Enabled travels.** The copy preserves the active flag; import-time force-inactive is the bundle importer's rule, not this module's.

## Consumers

`backend/migrations/m0004` + `m0005` (`copy_legacy_tables`), `bundle/channel_credential_tables.py` (`legacy_rows_to_generic` for pre-4d bundles), `tests/channel/test_generic_credential_store.py::test_legacy_tables_are_copied_once_with_secrets_decoded`.
