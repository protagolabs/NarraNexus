---
code_file: backend/routes/agents_llm_config.py
last_verified: 2026-07-09
stub: false
---

## 2026-07-09 — per-agent LLM config endpoints

Sub-router (mounted under ``/api/agents`` via [[agents]]) for the per-agent LLM
overrides that back the chat-page model/framework quick-switch + detailed panel.
Delegates writes to [[agent_slot_service]].

Endpoints:
- ``GET /{agent_id}/llm-config`` — per-slot view for agent + helper:
  ``{inheriting, effective, override, owner_default}``. ``effective`` is the
  override if present else the owner default. The provider dropdown options come
  from the existing ``GET /api/providers`` (not duplicated here).
- ``PUT /{agent_id}/llm-config/{slot_name}`` — set one slot override.
- ``DELETE /{agent_id}/llm-config/{slot_name}`` — reset that slot to inherit;
  ``slot_name='all'`` clears both.

Design decisions / gotchas:
- **Ownership 403**: ``_require_owner`` asserts ``agents.created_by`` == the
  caller (``resolve_current_user_id``). Changing an agent's runtime brain/billing
  is owner-only.
- **Cloud staff-gate**: mirrors the framework-switch gate in [[providers]] — in
  cloud a non-staff caller may not bind an OAuth-source provider (it would ride
  the shared CLI credentials).
- **No hot-reload**: config is resolved per run from the DB, and
  ``set_user_config`` is ContextVar/task-scoped (can't reach a running loop), so a
  change here applies on the agent's NEXT run. The handler says so explicitly.
