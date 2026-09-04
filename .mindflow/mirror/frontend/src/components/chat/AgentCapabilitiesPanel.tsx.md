---
code_file: frontend/src/components/chat/AgentCapabilitiesPanel.tsx
last_verified: 2026-09-04
stub: false
---

# chat/AgentCapabilitiesPanel.tsx — per-agent capability switches

## Intent

The owner's view of `/api/agents/{id}/capabilities` (plugin platform batch 5c): every registered module with its icon, description, builtin/plugin badge and a switch; base modules show a lock. Each toggle writes immediately and reloads; the budget line compares the enabled set's declared prompt cost with the builtin baseline and turns amber past 2×. Opened from the chat header's detail menu next to "Model & framework" (`ChatHeader.onOpenCapabilities`), mounted by `ChatPanel`. Strings under `chat.capabilities.*` in all ten locales.
