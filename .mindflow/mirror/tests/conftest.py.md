---
code_file: tests/conftest.py
last_verified: 2026-11-26
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

## 2026-11-25 — provider config proxies unpinned per test

`api_config.openai_config` / `claude_config` / … are `_ConfigProxy` objects that
resolve each read from the current task's ContextVar (else the global holder).
`monkeypatch.setattr(proxy, field, x)` records the old value through the
proxy's `__getattr__` and, on undo, writes it back as a real instance
attribute — so the field stays pinned for every later test and per-task
configs become invisible ("green alone, red in the full run"; hit by the
decision-credential tests after `test_structured_blocklist_isolation.py` and
`test_nexus_inprocess_steering.py`). An autouse teardown calls
`api_config.unpin_config_proxies()` after each test (the class owns the
knowledge of which attributes are its own); it runs after monkeypatch's undo
because monkeypatch is set up later and torn down first.

## 2026-11-26 — no real decision-key issuer in any test

`integrations.free_tier.decision_keys.decision_key_issuer()` is built once per
process (`functools.cache`) from the environment: cloud mode plus
`FREE_TIER_WALLET_API_URL` / `FREE_TIER_WALLET_API_TOKEN`. It must be
repo-wide, not narrative-local, because the tests that reach
`NarrativeService.select` span `narrative/`, `agent_runtime/`, `module/`,
`migration/` and `nx_kernel/`: a developer shell in cloud mode with the wallet
variables set would otherwise freeze a real issuer — and real quota-api calls —
into all of them. `_no_real_decision_key_issuer` removes the two
`FREE_TIER_WALLET_*` variables from every test's environment and clears the
factory's cache before and after each test. Same shape as
`_clear_health_cache`: the cache is only touched when the module is already
imported (looked up in `sys.modules`), so runs that never load it pay nothing.
Exit: a test that wants an issuer sets the variables itself or injects one
(the routing tests monkeypatch `merged_router.decision_key_issuer`).
