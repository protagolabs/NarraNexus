---
code_file: tests/backend/test_agents_channel_summary.py
last_verified: 2026-08-24
stub: false
---

# test_agents_channel_summary.py — Agent directory channel projection

## Why it exists

The Dashboard must not query every channel credential endpoint once per Agent.
This route-level test locks `GET /api/auth/agents` to a compact batched
`bound_channels` projection. A credential row counts as bound even when it is
inactive, matching the user's binding state rather than runtime health.

Channel presence is integration metadata and remains private: a public Agent
owned by another user is visible in the directory, but its channel bindings
must be returned as an empty list.
