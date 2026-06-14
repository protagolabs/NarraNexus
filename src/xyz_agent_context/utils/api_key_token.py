"""
@file_name: api_key_token.py
@author: NarraNexus
@date: 2026-06-11
@description: Token generation, parsing, and SHA256 hashing for agent API keys.

External API protocol (v0.3). Token format:

    nxk_apk_<key_id_12chars>_<random_url-safe-base64_64chars>

  `nxk_`     — namespace prefix the request middleware uses to detect that
               this is one of our tokens (vs JWT `eyJ...` or Manyfold's
               opaque byte string).
  `apk_<id>` — short identifier embedded in the plaintext so the middleware
               can look up the DB row in O(1) instead of brute-force hashing.
               `apk_<random12>` matches the DB row's `key_id` column.
  `<random>` — 64 url-safe-base64 chars of CSPRNG entropy (~384 bits). DB
               only stores SHA256(full_token); plaintext leaves the server
               exactly once (on creation/rotate response).

The middleware does:

    full_token = strip_bearer(authorization_header)
    if not full_token.startswith("nxk_"):
        return 401
    key_id = extract_key_id(full_token)       # "apk_<12>"
    row = SELECT * FROM agent_api_keys WHERE key_id = ?
    if not row or row.revoked_at: return 401
    if row.expires_at and row.expires_at < now: return 401
    if SHA256(full_token) != row.token_hash: return 401
    # row.agent_id + row.scopes get propagated to request.state

Constant-time compare is used for the SHA256 check to defeat timing
attacks (`hmac.compare_digest`).
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
from dataclasses import dataclass
from typing import Optional


# Public prefix that lets the middleware recognise our tokens at first
# glance. Three-segment design (nxk_ / apk_<id> / <random>) so the key_id
# can be extracted with simple string splitting; no regex needed in the
# hot path.
TOKEN_NAMESPACE_PREFIX = "nxk_"
KEY_ID_PREFIX = "apk_"

# Lengths chosen so the token fits in a single HTTP header line comfortably
# while providing well over 128 bits of secret entropy.
#
# key_id uses HEX (0-9 a-f) — NOT url-safe-base64 — so it never contains
# underscores. Important: the plaintext token uses `_` as the field
# separator (nxk_apk_<key_id>_<secret>), so the key_id alphabet must NOT
# include `_` or splitting in `parse_token` breaks. Secret uses url-safe
# (which CAN contain `_`) — the parser doesn't split the secret, so that's
# fine. 12 hex chars = 48 bits of randomness for the key_id; combined
# with the secret's 384+ bits of entropy, total token strength stays well
# beyond what matters here (the SHA256 check is the real guard).
KEY_ID_RANDOM_LEN = 12   # hex chars of randomness in the key_id
SECRET_RANDOM_LEN = 64   # url-safe-base64 chars of randomness in the secret


@dataclass(frozen=True)
class MintedToken:
    """Result of generating a fresh token.

    `plaintext` is the full token string returned to the owner ONCE. After
    we return it from the create/rotate endpoint, the platform forgets it.
    `key_id` and `token_hash` are persisted in DB.
    """

    plaintext: str
    key_id: str
    token_hash: str
    token_prefix: str  # first ~12 chars for UI display


def mint_token() -> MintedToken:
    """Generate a fresh token suitable for `agent_api_keys` storage.

    Format: ``nxk_apk_<12 hex chars>_<64 url-safe chars>``.

    Each call generates fresh randomness on both segments; collision on
    the 12-char hex key_id alone has ~2^-48 odds (acceptable; the DB
    UNIQUE constraint on `key_id` will reject the duplicate and the
    caller can retry).
    """
    key_id = KEY_ID_PREFIX + _random_hex(KEY_ID_RANDOM_LEN)
    secret = _random_urlsafe(SECRET_RANDOM_LEN)
    plaintext = f"{TOKEN_NAMESPACE_PREFIX}{key_id}_{secret}"
    token_hash = hashlib.sha256(plaintext.encode("utf-8")).hexdigest()
    token_prefix = plaintext[: len(TOKEN_NAMESPACE_PREFIX) + len(key_id) + 1]
    return MintedToken(
        plaintext=plaintext,
        key_id=key_id,
        token_hash=token_hash,
        token_prefix=token_prefix,
    )


def parse_token(plaintext: str) -> Optional[str]:
    """Pull the `key_id` out of a plaintext token, or return None if the
    string doesn't look like one of ours.

    Used by the middleware on every external API request — must be cheap
    and safe on adversarial input.
    """
    if not isinstance(plaintext, str):
        return None
    if not plaintext.startswith(TOKEN_NAMESPACE_PREFIX):
        return None
    # The plaintext is `nxk_apk_<random12>_<random64>`. Splitting on `_`
    # gives ['nxk', 'apk', '<random12>', '<random64>']; we need parts 1
    # and 2 joined to form `apk_<random12>` which equals the DB key_id.
    parts = plaintext.split("_")
    if len(parts) < 4:
        return None
    if parts[1] != KEY_ID_PREFIX.rstrip("_"):
        return None
    key_id = f"{KEY_ID_PREFIX}{parts[2]}"
    return key_id


def verify_token_hash(plaintext: str, stored_hash: str) -> bool:
    """Constant-time SHA256 comparison.

    Always returns False if either input isn't a non-empty string, so the
    middleware can use this without pre-checking for None / empty.
    """
    if not plaintext or not stored_hash:
        return False
    calculated = hashlib.sha256(plaintext.encode("utf-8")).hexdigest()
    return hmac.compare_digest(calculated, stored_hash)


def _random_urlsafe(n_chars: int) -> str:
    """N url-safe characters of cryptographic randomness.

    `secrets.token_urlsafe` returns a base64-url string ~1.33 chars per
    input byte. We oversample then truncate to the exact length we want.
    """
    # ceil(n_chars * 3 / 4) bytes give us at least n_chars after b64.
    n_bytes = (n_chars * 3 + 3) // 4
    return secrets.token_urlsafe(n_bytes)[:n_chars]


def _random_hex(n_chars: int) -> str:
    """N hex characters (0-9 a-f) of cryptographic randomness.

    Used for the key_id segment of the token, where the alphabet must
    NOT contain `_` (the field separator). Each char is 4 bits.
    """
    # ceil(n_chars / 2) bytes give us n_chars hex digits.
    n_bytes = (n_chars + 1) // 2
    return secrets.token_hex(n_bytes)[:n_chars]
