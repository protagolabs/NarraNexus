---
code_file: tests/conftest.py
last_verified: 2026-10-29
stub: false
---

# tests/conftest.py

## 2026-09-07 — builtin loading moved to the repo-root conftest

Applies to every test tree, not only tests/.

## 2026-10-29 — login_ips throttle maps cleared per test

`backend.middleware.login_ips` keeps two in-process (user, ip) throttle maps:
`_last_recorded` (login / activity IPs) and `_last_refused` (the
`login_refused` audit rows). The refused-login path neutralises a non-public
IP to `-`, so every TestClient-driven refusal shares the key `(user, "-")`,
and several modules log the same test user in — a claim left by one test would
silently swallow the next test's write ("green alone, red in the full run").
`_clear_login_ip_throttles` clears both maps before and after every test. Same
shape as `_clear_health_cache` / `_fresh_netmind_login_ip_bucket`: it only
touches the module when something already imported it (looked up through
`sys.modules` on each reset, never cached — tests replace the maps with
`monkeypatch.setattr`), so runs that never load backend pay nothing.

## 2026-10-28 — fresh netmind-login per-IP bucket per test

`backend.routes.auth` now charges a module-level per-IP bucket on
`POST /api/auth/netmind-login` (PR #420 review M-4). Every TestClient request
comes from the same peer (`testclient`), and several test modules
(netmind-login route, account suspension, guide-agent login hook, legacy-auth
removal, body-size gate, signup abuse limits) log in through that route, so a
module singleton would fill across modules and surface as an order-dependent
429 ("green alone, red in the full run"). `_fresh_netmind_login_ip_bucket`
swaps in a fresh limiter at the shipped/env-resolved limit around every test,
and like `_clear_health_cache` it only touches the module if something
already imported it.
