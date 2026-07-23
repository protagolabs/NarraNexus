---
code_file: src/xyz_agent_context/agent_framework/model_probe_ledger.py
last_verified: 2026-07-23
stub: false
---

## 2026-07-23 — `all_ledger_models(source)`

Companion read to `ledger_models` that ignores the per-protocol pass/fail
filter and returns EVERY catalogued id for a source (sorted; same
`system_pool`→`netmind` aliasing). Backs the `NARRANEXUS_OFFER_ALL_MODELS`
dev opt-in in [[model_catalog]] — see that mirror for the rationale (snapshot
probed from one network under-counts on other networks). Pure read, no probing.

# agent_framework/model_probe_ledger.py — the probe dedup cache (read/write)

## Why it exists

The pure read/write layer for `model_probe_ledger.json` — the committed record
of, per (provider source, model id), which protocols (`openai`/`anthropic`) the
model actually answers on. Split from [[model_sync]] (which owns the probing) so
[[model_catalog]] can READ the ledger without importing the httpx/probe code
(avoids a circular import and keeps the read path dependency-free).

## How it works / design

- The JSON file is **committed** = the release-time snapshot, so a fresh local /
  DMG install ships with known-good per-protocol lists and never probes on first
  run. The cloud daily job and the local "Update" button rewrite it at runtime
  (the DB `user_providers.models` rows are the durable store; the file is the
  dedup cache that survives until a redeploy reseeds it from the committed copy).
- `ledger_models(source, protocol)` returns only ids where `entry[protocol] ==
  "pass"`. `system_pool` is aliased to the `netmind` entry (same backend).
- `save_ledger` writes sorted, pretty JSON so diffs stay clean across runs.

## Gotchas

- Missing/corrupt file → empty skeleton (caller falls back to hardcoded
  defaults), so a wiped runtime never hard-fails.
- It's a `.json` data file, not source — only the model ids + pass/fail +
  display/context metadata live here, no logic.
