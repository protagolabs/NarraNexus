---
code_file: tests/channel/test_credential_breaker.py
last_verified: 2026-08-04
---

# test_credential_breaker.py — fast-death breaker pin (2026-08-04)

Pins the watcher-level circuit breaker added for the prod cleared-secret
incident (subscriber dies silently on start, watcher restarted it every
poll forever). See the dated 2026-08-04 entry in channel_trigger_base.py.md
for the design. Coverage: trip after N consecutive fast deaths (restarts
stop), healthy lifetime resets the streak, backoff expiry re-probes,
second trip escalates to the next schedule tier, credential change clears
immediately, removed credential purges all breaker state. Drives the real
`start()`/watcher with sub-second poll intervals — the death shape is a
`_subscribe_loop` override that returns instantly (the empty-secret
shape: return, not raise).
