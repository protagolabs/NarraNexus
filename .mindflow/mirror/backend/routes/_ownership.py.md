---
code_file: backend/routes/_ownership.py
stub: false
last_verified: 2026-08-10
---

## Why it exists

Canonical agent-ownership verification for backend routes. Every channel
route (slack/lark/telegram/discord/wechat/narramessenger) and home_assistant
had pasted its own `_verify_agent_ownership` / `_require_agent_owner` — the
copies drift apart, which is exactly the bug `AgentRepository.resolve_owner`
already fixed on the agent side. This is its backend-route counterpart, built
ON that seam.

## Model

Ownership = `agents.created_by` (via `AgentRepository.resolve_owner`). Two
surfaces because the callers historically returned two shapes:
- `check_owned(request, agent_id) -> str | None` — channel routes wrap the
  string in `{"success": False, "error": ...}`.
- `assert_owned(request, agent_id)` — raises HTTPException(404 unknown / 403
  non-owner); home_assistant-style.

## Decisions / gotchas

- **Local mode = no enforcement.** No `request.state.user_id` (auth middleware
  sets it only in cloud) → returns None / no-op, preserving every copy's prior
  convention (rule #7-adjacent).
- **Fail-closed on unresolvable owner.** `resolve_owner` returns "" for both
  "unknown agent" and "lookup error"; both deny (404) rather than leak — safer
  than the old raw `get_one` which 500'd on a DB error.
