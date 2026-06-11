---
code_file: src/xyz_agent_context/utils/api_key_token.py
last_verified: 2026-06-11
stub: false
---

# api_key_token.py

Token format, generation, parsing, and constant-time verification for the
agent_api_keys system used by the external API protocol (v0.3).

## Why this exists

External integrators need a token they can put in `Authorization: Bearer ...`
to call `/v1/external/chat/completions`. The token must:

1. Be distinguishable from JWT (`eyJ...`) and Manyfold's opaque gateway token
   at first byte by the middleware (so it routes to the right verifier).
2. Be looked up in the `agent_api_keys` table in O(1) — no scanning, no
   bcrypt-style key-stretching at request time.
3. Never be recoverable from the DB even by us (only SHA256 stored).
4. Be hard for a timing attack to learn anything from a wrong guess.

Format `nxk_apk_<12 hex>_<64 url-safe>` solves all four:

- `nxk_` prefix → middleware can hand non-our tokens straight to JWT verifier.
- `apk_<12 hex>` is the `key_id` column; hex (not base64-url) so the
  underscore-separated format parses unambiguously.
- 12-hex key_id = 48-bit collision space; DB UNIQUE on `key_id` rejects the
  one-in-2^48 dup so the route layer can just retry.
- 64-char url-safe random secret = 384+ bits of secret entropy.
- DB stores SHA256(plaintext); plaintext is only ever returned from the
  create + rotate endpoints, once each.

## Upstream / Downstream

**Consumed by:**
- `backend/routes/agents_api_keys.py` — create / rotate endpoints call
  `mint_token()`.
- `backend/auth.py` (future, Step 5) — the external API middleware calls
  `parse_token()` to extract `key_id`, then `verify_token_hash()` for
  constant-time SHA256 compare.

**Depends on:**
- `secrets.token_hex` / `secrets.token_urlsafe` (CSPRNG).
- `hashlib.sha256`.
- `hmac.compare_digest` for timing-safe comparison.

Pure stdlib — no project imports, no DB.

## Design decisions

**Two alphabets, two purposes.** key_id uses hex (16-char alphabet) because
the plaintext token splits on `_` and base64-url's `_` character would corrupt
the parse. Earlier draft used url-safe for both segments and the first
self-test caught the bug immediately. The secret stays url-safe because the
parser doesn't split it; only the SHA256 compare matters there.

**Constant-time SHA256 compare.** `verify_token_hash` uses
`hmac.compare_digest`. SHA256 isn't a password hash (no key stretching), so
the entropy of the secret has to do all the work — but at least we don't
leak per-byte timing info to a guessing attacker.

**No expiry baked into the token.** Unlike JWT, we don't put `exp` in the
plaintext. The DB row owns the truth (`expires_at` column), and the
middleware checks it on every request. This lets the rotate endpoint
"extend" the old token's expiry without re-minting plaintext — simpler.

**Token oversamples then truncates.** `secrets.token_urlsafe(N)` returns a
string of *at least* the requested length, and the public-API-friendly
`KEY_ID_RANDOM_LEN` / `SECRET_RANDOM_LEN` constants are exact char counts —
we want the on-the-wire token to be a fixed length so the prefix display in
the UI is uniform. The helpers compute the right number of input bytes from
the desired character count, then `[:n_chars]` truncate. Truncation does
NOT reduce the CSPRNG quality of any given output byte.

## Gotchas

**Don't ever log the plaintext.** The `MintedToken` dataclass contains it.
The create route's logger only emits `key_id`, never `plaintext`. If you
add a debug log of `MintedToken`, you've broken the "plaintext leaves the
server exactly once" promise.

**SHA256 is fine here, BCrypt would be wrong.** Password hashes need to be
slow to defeat dictionary attacks against weak human-chosen secrets.
Our secrets are 384+ bits of CSPRNG output — no dictionary to attack.
Per-request bcrypt would just add p99 latency without security benefit.

**Beware the parser on adversarial input.** `parse_token` deliberately
returns `None` instead of raising for any malformed input. The middleware
treats `None` as "401 invalid_token" without needing a try/except. Don't
"improve" the parser to raise — it's the trust boundary.

**Collision retry, not collision-impossible.** 48-bit key_id collisions
will happen at scale (one in ~2^24 keys, by birthday paradox). The DB
UNIQUE constraint surfaces the dup; the create route must catch that
and retry. Currently it doesn't — TODO for Step 5/6 once we wire the
route to actually be exercised under load. For now, prob(collision in
any reasonable single-tenant install) ≈ zero.
