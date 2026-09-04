---
code_file: backend/routes/channels/generic.py
last_verified: 2026-09-04
stub: false
---

# routes/channels/generic.py — /api/channels/{channel}/…

## Intent

Schema / bind / credential / test / unbind / set-active for ANY channel in `ingress.channels`. Manager-backed (builtin) channels delegate bind/test/unbind to the data-access seam so their bespoke services keep validating with the external platform (the manager mirror keeps `channel_credentials` fresh) and read the public view from the generic table; plugin channels are validated against their schema (required fields) and stored directly. Ownership is `_ownership.check_owned`; unknown channel → 404. Shell-level (mounted by main.py), not plugin-owned. The six bespoke routers stay until 4d.

## 2026-09-04 · webhook transport (batch 4c)

`POST /{channel}/webhook/{agent_id}` — auth-exempt at the middleware (the binding's `webhook_secret` is the auth, token or HMAC), 404 for non-webhook channels or unbound agents, JSON body → `WebhookInbox.push`. Binding a webhook channel issues `webhook_secret` once (returned only at first bind, never in the credential view) plus `webhook_path`.
