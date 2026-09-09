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

## 2026-09-07 — webhook: header/HMAC only, uniform 401, rate limited, safe ids; bind conflict 409

The inbound webhook no longer accepts ?token= (the request line lands in every reverse-proxy access log). Unknown binding and bad secret both answer 401 (the distinction is logged) so the anonymous endpoint is not an agent-enumeration oracle; a sliding window per binding and per source address bounds the DB reads; agent_id is constrained to the safe id pattern.

The **source address** comes from `backend/routes/_client_ip.py::client_ip`, never `request.client.host`. Uvicorn runs without `--proxy-headers` behind the deploy stack's nginx, so the socket peer is the SAME container address for every cloud request: keyed on it, the 600/min limiter is one GLOBAL bucket, and any single anonymous caller can 429 every agent's inbound webhooks at once — a limiter that reads as protection while measuring nothing. `_client_ip` is the shared implementation (the auth funnel is the other caller): the proxy-hop count is a property of the deployment and must have exactly one home.

The middleware wiring is tested end to end in `tests/backend/test_channel_webhook_middleware.py` with the REAL `auth_middleware` and cloud mode forced — a hand-rolled identity middleware cannot tell "exempt by design" from "never behind auth at all".
