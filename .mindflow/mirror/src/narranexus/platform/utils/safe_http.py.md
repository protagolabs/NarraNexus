---
code_file: src/narranexus/platform/utils/safe_http.py
last_verified: 2026-10-29
stub: false
---

# safe_http.py — the single SSRF-gated redirect walk + connect-time pinning

## 2026-10-29 — connect-time pinning (DNS rebinding, incident P0-1)

Every gated site used to check a URL with [[url_safety]] and then let its HTTP
client resolve the name AGAIN to connect. A short-TTL attacker domain answers
public to the check and `169.254.169.254` / a docker service to the dial — the
backend container is the one that can still reach IMDS, so this was the
incident's P0. The fix moves the check INTO the dial:

- `PublicOnlyNetworkBackend` (httpcore backend): `connect_tcp` calls
  `url_safety.resolve_public_addresses` (same rules as the pre-check — one
  implementation) and opens the socket to a validated address. TLS SNI,
  certificate verification and the Host header still use the hostname,
  because httpcore takes those from the request origin, not from the address
  the socket went to. Unix sockets are refused.
- One connect-timeout budget covers resolution and every dial (the stock
  backend resolved inside its own timeout; an attacker-run DNS server must not
  hang a request past it — resolution timing out raises `ConnectTimeout`).
  Addresses are de-duplicated (in [[url_safety]]), interleaved IPv6/IPv4
  keeping the resolver's first preference, and each attempt gets
  `remaining / addresses_left` of the budget — the stand-in for the
  happy-eyeballs fallback the stock backend did, so a black-holed first
  address (dead IPv6) cannot consume the whole timeout. Trade-off: with N
  addresses the first gets 1/N of the budget, so a live-but-slow first address
  can fall through to the next one where stock happy-eyeballs would have
  raced it.
- `PublicOnlyTransport` builds an `AsyncHTTPTransport`'s pool around that
  backend (without calling `super().__init__`, which would build and discard a
  pool + CA bundle per instance). Its SSL context keeps `trust_env=True`, so
  `SSL_CERT_FILE` / `SSL_CERT_DIR` behave exactly as for a default client.
  No proxy support on purpose: through a proxy the PROXY resolves the name and
  the pin is gone (httpx already ignores `*_PROXY` env once a transport is
  passed).
- `PublicOnlyResolver` + `egress_connector()`: the aiohttp equivalent —
  `TCPConnector` dials exactly what its resolver returns. aiohttp never sends a
  literal-IP host to a resolver; literals cannot rebind, so callers'
  pre-check stays the gate for those. `UnsafeUrlError` surfaces raw from
  `session.get` (not wrapped in `ClientError`), so callers catch it explicitly.
- `egress_client()` / `egress_connector()` are deployment-aware: pinned in
  cloud, plain locally (desktop/run.sh legitimately reach localhost — same
  contract as `assert_public_http_url_in_cloud`). They reject
  `transport` / `proxy` / `mounts` / `resolver` kwargs that would bypass the pin.
  Sites whose own contract is "always public" (URL artifact fetchers, MCP
  validate with `enforce_public_url`) use `PublicOnlyTransport` directly.

Wired into: URL artifact fetchers ([[embed_probe]], [[page_text]]), transcription
[[openai_multipart]], provider Test probes in [[registry]] (user-sourced cards
only — platform cards point at our internal gateway by design), MCP validate in
[[mcp_repository]], [[ha_client]], NarraMessenger bind in
[[_narramessenger_service]].

`egress_sdk_http_client(factory)` covers the helper-LLM SDK clients
(openai / anthropic): pass the SDK's own `DefaultAsyncHttpxClient` so its
timeout default survives (its connection limits do not — httpx uses the passed
transport's pool, i.e. PublicOnlyTransport's 100/20; a client is built per
call anyway); in cloud it returns that client with the
pinned transport and `follow_redirects=False` (the SDK default follows
redirects — a public endpoint could 302 into the network), locally None.
Only configs the resolver marked `user_endpoint` use it (platform cards point
at our internal gateway).

Not covered here (no Python seam, or a different problem): stored
NarraMessenger/matrix URLs used without re-check after bind; executor-side
CLI/MCP connections and in-app Chromium — those need network-level egress
filtering.

aiohttp tests probe `loop.sock_connect` (aiohttp dials through
aiohappyeyeballs before `create_connection(sock=...)`, so a `create_connection`
spy would never see the dial).

Tests: `tests/utils/test_public_only_transport.py` (the incident's acceptance
case — first answer public, second answer metadata → refused, nothing dialled;
dial goes to the validated IP with hostname SNI + Host; proxy env ignored;
local factory unpinned) plus one rebinding test per wired site, each verified
red with the pin reverted.

## Why it exists

The embed probe and the page-text extractor both need "GET a URL, follow
redirects, but SSRF-check every hop." That loop was copy-pasted into both
(PR #137 review flagged it) — and a duplicated security walk means the gate
can be fixed in one copy and forgotten in the other. This is the ONE
implementation; `embed_probe.probe_url` and `page_text.fetch_page_text` (and
the future RenderService / streaming browser) all build on it.

## Design

- `safe_stream_get(client, url, ...)` is an `@asynccontextmanager` that yields
  the FINAL (non-redirect) response with its body UNREAD, so the caller
  chooses: read headers only (probe) or a bounded body slice (text). Following
  redirects manually is what lets `assert_public_http_url` run on each hop.
- Lives in `utils/` (not the artifact package) so RenderService can reuse it;
  kept SEPARATE from [[url_safety.py]] so that module stays stdlib-only (its
  contract) — this one owns the httpx dependency.
- `BROWSER_UA` / `MAX_REDIRECTS` are the single source (callers stopped each
  keeping a private copy). `RedirectLimitError` signals the loop overran;
  callers catch it and degrade.

## Gotcha

The `client` MUST be `follow_redirects=False` — we follow manually. Passing a
client that auto-follows would bypass the per-hop SSRF gate.
