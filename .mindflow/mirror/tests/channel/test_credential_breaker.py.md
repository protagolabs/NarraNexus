---
code_file: tests/channel/test_credential_breaker.py
last_verified: 2026-08-04
---

# test_credential_breaker.py — subscriber isolation pin (2026-08-04)

Pins both gates added for the prod cleared-secret incident: the
`should_start_subscriber` pre-flight and the fast-death breaker. Design and
incident in the 2026-08-04 entry of channel_trigger_base.py.md.

Structure is deliberate. Most cases drive the breaker's state machine
**directly** (`_armed` fakes the marks a just-died subscriber leaves, so a
"healthy" lifetime is a subtraction rather than a sleep) — exact tier
assertions, no wall-clock flake, no fake clock patched into asyncio's own
time source. Only three cases run the real watcher, and only to prove
wiring: restarts stop after a trip, an unstartable credential never starts,
and a re-fixed credential drops its pre-flight mark while still isolated.

The fingerprint cases carry the sharpest edge and each pins a way the
breaker was (or could be) silently defeated: sampling the watcher's cache
instead of the started-with credential, counting a write-heavy column
(Matrix's `since_token`) as a re-bind, resetting the escalation tier on
every fingerprint-triggered clear, and — the one that bites hardest — a
credential whose fallback fingerprint embeds its object address, which
would read as "re-bound" on every single poll.
