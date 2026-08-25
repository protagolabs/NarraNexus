---
code_file: tests/backend/test_agents_llm_summary.py
last_verified: 2026-08-24
stub: false
---

# test_agents_llm_summary.py — Effective LLM summary contract

## Why it exists

The Agents directory needs the effective framework and model for every row,
but fetching the per-agent configuration endpoint once per row would create an
N+1 request pattern. This route-level regression test locks the enriched
`GET /api/auth/agents` response to the same inheritance rule used at runtime:
an agent override wins, otherwise the owner's agent slot is shown.
