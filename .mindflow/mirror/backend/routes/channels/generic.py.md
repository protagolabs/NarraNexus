---
code_file: backend/routes/channels/generic.py
last_verified: 2026-09-07
stub: false
---

# routes/channels/generic.py — /api/channels/{channel}/…

## Intent

Schema / bind / credential / test / unbind / set-active for ANY channel in `ingress.channels`. Manager-backed (builtin) channels delegate bind/test/unbind to the data-access seam so their bespoke services keep validating with the external platform (the manager mirror keeps `channel_credentials` fresh) and read the public view from the generic table; plugin channels are validated against their schema (required fields) and stored directly. Ownership is `_ownership.check_owned`; unknown channel → 404. Shell-level (mounted by main.py), not plugin-owned. The six bespoke routers stay until 4d.

## 2026-09-04 · webhook transport (batch 4c)

`POST /{channel}/webhook/{agent_id}` — auth-exempt at the middleware (the binding's `webhook_secret` is the auth, token or HMAC), 404 for non-webhook channels or unbound agents, JSON body → `WebhookInbox.push`. Binding a webhook channel issues `webhook_secret` once (returned only at first bind, never in the credential view) plus `webhook_path`.

## 2026-09-04 · the one binding surface (batch 4d.3)

The six bespoke `/api/<channel>/{bind,credential,test,unbind,set-active}` routes are gone; every channel — builtin or plugin — binds, reads, tests, unbinds and toggles here. What changed: bodies enforce the safe agent_id shape the old routes had (`_SAFE_ID_PATTERN`, 422 before any lookup); a bind body is validated against the descriptor's `bind_fields` (`credential_store.validate_bind_fields`: unknown names rejected fail-closed because they would reach `do_bind(**fields)`, required present, select within options) BEFORE the channel service runs; manager-backed bind/test/unbind still delegate to the data-access seam so the service's envelope (Lark's `error_detail`/`warnings`, NarraMessenger's flat unbind) reaches the UI verbatim; set-active flips the generic store row for every channel (no more per-manager `set_enabled`/`set_is_active` lookup); the schema view carries `bind_fields` next to the stored `fields`.

## 2026-09-07 — bind conflict → 409

CredentialConflict from the store becomes HTTP 409 with the product-level message ('this bot is already bound to another agent') instead of a driver IntegrityError 500.
