---
code_file: backend/migrations/m0004_channel_credentials_backfill.py
last_verified: 2026-09-04
stub: false
---

# m0004 — channel_credentials backfill

## Intent

One-shot, idempotent copy of the active bespoke channel bindings into `channel_credentials` (dual-write phase of batch 4b).
