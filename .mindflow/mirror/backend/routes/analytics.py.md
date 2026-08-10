---
code_file: backend/routes/analytics.py
last_verified: 2026-08-10
stub: false
---

# analytics.py — authenticated first-party event ingestion

The browser reports only an enumerated set of lifecycle events here. Identity
comes from auth middleware; arbitrary event names and free-form properties are
rejected. The route writes operational facts to the product database through
the shared analytics API, independently of PostHog.

Event IDs are client-generated idempotency keys. Message text, credentials,
email, and other PII must never be added to this request model.
