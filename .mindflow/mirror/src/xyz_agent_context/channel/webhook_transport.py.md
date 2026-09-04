---
code_file: src/xyz_agent_context/channel/webhook_transport.py
last_verified: 2026-09-04
stub: false
---

# channel/webhook_transport.py — webhook channels

## Intent

`WebhookChannelTriggerBase`: a channel trigger whose `connect()` drains the inbox for its credential instead of holding a socket, and whose `load_active_credentials()` is the generic store's active rows — so a webhook channel implements only the parsing half of the trigger contract. `verify_webhook` is the endpoint's authentication: the binding's `webhook_secret` as a plain token (`X-Webhook-Token` / `?token=`) or an HMAC-SHA256 of the raw body (`X-Webhook-Signature: sha256=…`), constant-time compared. Constructed as `cls(max_workers=3)` like every channel trigger.
