---
code_file: frontend/src/components/awareness/GenericChannelConfig.tsx
last_verified: 2026-09-04
stub: false
---

# awareness/GenericChannelConfig.tsx — schema-driven channel panel

## Intent

Renders a channel's credential schema (GET /api/channels/{channel}/schema) as the bind form — secrets as password inputs, selects, booleans, required check — and, once bound, the public identity fields with test (when supported) / activate-deactivate / unbind through the generic API; a webhook channel shows its inbound path. What a channel plugin's Channels row renders (`makeGenericChannelConfig`); the six builtins keep their bespoke panels until 4d.

## 2026-09-04 · bind form from `bind_fields` (batch 4d.3)

The form renders `schema.bind_fields` (what a bind call takes), the bound view still lists `schema.fields` identity values — the two differ for builtin channels, so a manager-backed channel rendered generically shows the right inputs.
