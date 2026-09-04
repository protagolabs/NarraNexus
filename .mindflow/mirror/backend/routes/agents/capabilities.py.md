---
code_file: backend/routes/agents/capabilities.py
last_verified: 2026-09-04
stub: false
---

# routes/agents/capabilities.py — the owner's capability switches

## Intent

GET lists every registered module with its declaration, state (enabled / default / explicit / locked) and the context budget; PUT flips one module (`{"enabled": bool}`, base modules 400); DELETE returns a module to the default rule. Owner-gated exactly like `llm_config` (404 unknown agent, 403 not the owner). Mounted by `agents/core.py`; the PUT body is capped in `middleware/body_size.py`. Frontend: `components/chat/AgentCapabilitiesPanel.tsx`.
