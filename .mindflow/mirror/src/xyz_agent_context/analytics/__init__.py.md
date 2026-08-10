---
code_file: src/xyz_agent_context/analytics/__init__.py
last_verified: 2026-08-10
stub: false
---

# __init__.py (analytics)

## Why it exists

This module is the single first-party product-event write surface. Capture
sites call `track()`; the module applies the user's analytics opt-out, stamps
the process surface, and persists the event to `product_analytics_events`.

There is intentionally no vendor sink, SDK, network client, identify call, or
shutdown flush. Cloud writes to its configured RDS database. Local and desktop
write to their local SQLite database, so their product facts never leave the
machine.

## Upstream / downstream

- **Consumed by**: routes and runtime services that record activation,
  messaging, failure, and payment facts.
- **Depends on**: `analytics/surface.py`, `UserSettingsRepository`, and the
  shared database client.
- **Consumed downstream by**: `narranexus-data`, which has read-only access to
  the cloud RDS table. It cannot see local SQLite rows.

## Design decisions

**Database-only persistence**: removing the old sink seam makes the privacy
boundary structural rather than configuration-dependent. Legacy analytics
environment variables cannot reactivate telemetry because no external SDK or
adapter exists in the runtime or desktop bundle.

**Opt-out precedes persistence**: opted-out users produce no product-event
rows. The lookup remains best-effort so analytics never interrupts product
flow; insert failures are logged and swallowed independently.

**Known dimensions plus JSON**: query-critical dimensions are duplicated into
indexed columns. The compact JSON copy is retained for low-volume diagnosis,
but capture sites must never send message text, credentials, email addresses,
or other free-form PII.

**Idempotent event IDs**: caller-supplied IDs are checked before insert and the
database unique key is the final concurrency guard. Calls without a stable ID
receive a UUID.

## Gotchas

- `surface` describes where the database write happened; it is not a routing
  instruction and never sends an event elsewhere.
- The local analytics opt-out still matters because it controls local SQLite
  persistence even though external telemetry no longer exists.
- `track()` is observational and must never break login, chat, or payment.
