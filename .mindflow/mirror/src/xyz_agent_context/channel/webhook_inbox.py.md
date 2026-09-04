---
code_file: src/xyz_agent_context/channel/webhook_inbox.py
last_verified: 2026-09-04
stub: false
---

# channel/webhook_inbox.py — the webhook transport inbox

## Intent

`channel_webhook_events` as a queue: the backend API process pushes a channel's inbound webhook events, the channel trigger (workers process) pulls and claims them in arrival order (`claimed_at`), so the hand-off crosses processes. A DB table on purpose — one host today, the seam a broker replaces later (rule #20). `purge_claimed` keeps the audit window short.
