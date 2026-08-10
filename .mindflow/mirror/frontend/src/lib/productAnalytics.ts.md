---
code_file: frontend/src/lib/productAnalytics.ts
last_verified: 2026-08-10
stub: false
---

# productAnalytics.ts — non-blocking first-party browser capture

Centralizes authenticated event ingestion, assigning a per-tab session ID and
an idempotency ID per fact. Capture is fire-and-forget so measurement cannot
delay login, chat, rendering, or checkout. Payloads contain only controlled
identifiers and timings, never product content.

Requests use Fetch `keepalive` because `/pay` records checkout-open immediately
before replacing the page with Stripe; without it navigation can cancel the
only conversion signal.
