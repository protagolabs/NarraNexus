---
code_file: frontend/packages/ui-kit/src/client.ts
last_verified: 2026-09-04
stub: false
---

# @narranexus/ui-kit — client.ts

`ChatClient` (framework-agnostic embedding transport): `history(limit)` reads `/api/agents/{id}/chat-history` (X-User-Id locally, bearer on cloud) and `historyToMessages` turns events (trigger → user, final_output → assistant) into messages; `send(text, onEvent)` opens `/ws/agent/run` (`?x_user_id=` locally, token in payload on cloud), sends the app's run payload, accumulates `agent_response` and `agent_reply_delta` deltas, settles once on `complete`/`error`/`cancelled`/close, and `stop()` sends `{action: "stop"}`. `wsUrl()` derives the socket URL.
