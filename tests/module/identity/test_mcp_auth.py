"""
@file_name: test_mcp_auth.py
@author:
@date: 2026-08-10
@description: Tests for module/identity/mcp_auth.py — the ASGI auth middleware
on every module MCP server, and the NX_MCP_AUTH_MODE gating.

Pins the rollout contract:
- off (default): byte-identical behaviour, contextvar stays None
- audit: verifies + logs, NEVER rejects
- enforce: tool-call POSTs without a valid token are 401'd; GETs (SSE
  handshake) and /health stay open; a missing public key fails OPEN with a
  warning (deploy misconfiguration must not take the data plane down)
- a bearer whose declared user_id disagrees with the token's sub is invalid
"""
from __future__ import annotations

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ed25519
from starlette.applications import Starlette
from starlette.middleware import Middleware
from starlette.responses import PlainTextResponse
from starlette.routing import Route
from starlette.testclient import TestClient

from xyz_agent_context.module._mcp_identity import agent_id_headers
from xyz_agent_context.module.identity.mcp_auth import (
    IdentityAuthMiddleware,
    auth_mode,
    verified_caller,
)
from xyz_agent_context.module.identity.tokens import ISSUER_LOCAL, sign_identity_token

AGENT = "agent_39b2b72b823b"


def _keypair() -> tuple[bytes, bytes]:
    priv = ed25519.Ed25519PrivateKey.generate()
    priv_pem = priv.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    )
    pub_pem = priv.public_key().public_bytes(
        serialization.Encoding.PEM,
        serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    return priv_pem, pub_pem


def _provision(tmp_path, monkeypatch) -> bytes:
    """Write a public key file, point the verifier at it, return the private PEM."""
    priv_pem, pub_pem = _keypair()
    pub_file = tmp_path / "identity_ed25519.pub"
    pub_file.write_bytes(pub_pem)
    monkeypatch.setenv("NX_IDENTITY_PUBLIC_KEY_FILE", str(pub_file))
    return priv_pem


def _app() -> Starlette:
    async def echo(request):
        ident = verified_caller()
        return PlainTextResponse(ident.user_id if ident else "anon")

    return Starlette(
        routes=[
            Route("/messages/", echo, methods=["GET", "POST"]),
            Route("/sse", echo, methods=["GET"]),
            Route("/health", echo, methods=["GET", "POST"]),
        ],
        middleware=[Middleware(IdentityAuthMiddleware)],
    )


def _headers(priv_pem: bytes, user_id: str = "usr_1") -> dict:
    token = sign_identity_token(user_id, priv_pem, issuer=ISSUER_LOCAL)
    return agent_id_headers(AGENT, user_id=user_id, identity_token=token)


# ---------------------------------------------------------------------------
# mode parsing
# ---------------------------------------------------------------------------


def test_mode_defaults_to_off(monkeypatch):
    monkeypatch.delenv("NX_MCP_AUTH_MODE", raising=False)
    assert auth_mode() == "off"


def test_unknown_mode_reads_as_audit(monkeypatch):
    # A typo'd mode must surface (audit logs) rather than silently disable auth.
    monkeypatch.setenv("NX_MCP_AUTH_MODE", "enfroce")
    assert auth_mode() == "audit"


# ---------------------------------------------------------------------------
# off — byte-identical
# ---------------------------------------------------------------------------


def test_off_passes_everything_and_verifies_nothing(monkeypatch):
    monkeypatch.delenv("NX_MCP_AUTH_MODE", raising=False)
    client = TestClient(_app())
    r = client.post("/messages/")
    assert r.status_code == 200
    assert r.text == "anon"


# ---------------------------------------------------------------------------
# audit — verify, log, never reject
# ---------------------------------------------------------------------------


def test_audit_never_rejects_but_exposes_verified_identity(tmp_path, monkeypatch):
    priv = _provision(tmp_path, monkeypatch)
    monkeypatch.setenv("NX_MCP_AUTH_MODE", "audit")
    client = TestClient(_app())

    assert client.post("/messages/").status_code == 200  # tokenless: allowed
    r = client.post("/messages/", headers=_headers(priv))
    assert r.status_code == 200
    assert r.text == "usr_1"  # verified identity reached the handler


def test_audit_invalid_token_passes_as_anonymous(tmp_path, monkeypatch):
    _provision(tmp_path, monkeypatch)
    other_priv, _ = _keypair()
    monkeypatch.setenv("NX_MCP_AUTH_MODE", "audit")
    r = TestClient(_app()).post("/messages/", headers=_headers(other_priv))
    assert r.status_code == 200
    assert r.text == "anon"


# ---------------------------------------------------------------------------
# enforce
# ---------------------------------------------------------------------------


def test_enforce_rejects_tokenless_post(tmp_path, monkeypatch):
    _provision(tmp_path, monkeypatch)
    monkeypatch.setenv("NX_MCP_AUTH_MODE", "enforce")
    assert TestClient(_app()).post("/messages/").status_code == 401


def test_enforce_rejects_forged_token(tmp_path, monkeypatch):
    _provision(tmp_path, monkeypatch)
    other_priv, _ = _keypair()
    monkeypatch.setenv("NX_MCP_AUTH_MODE", "enforce")
    assert TestClient(_app()).post("/messages/", headers=_headers(other_priv)).status_code == 401


def test_enforce_accepts_valid_token(tmp_path, monkeypatch):
    priv = _provision(tmp_path, monkeypatch)
    monkeypatch.setenv("NX_MCP_AUTH_MODE", "enforce")
    r = TestClient(_app()).post("/messages/", headers=_headers(priv))
    assert r.status_code == 200
    assert r.text == "usr_1"


def test_enforce_leaves_gets_and_health_open(tmp_path, monkeypatch):
    _provision(tmp_path, monkeypatch)
    monkeypatch.setenv("NX_MCP_AUTH_MODE", "enforce")
    client = TestClient(_app())
    assert client.get("/sse").status_code == 200          # SSE handshake
    assert client.get("/messages/").status_code == 200
    assert client.post("/health").status_code == 200      # probes


def test_enforce_rejects_user_id_field_mismatch(tmp_path, monkeypatch):
    # The self-declared bearer user_id must agree with the proven sub —
    # a mismatch is a forged field, not an unknown.
    priv = _provision(tmp_path, monkeypatch)
    monkeypatch.setenv("NX_MCP_AUTH_MODE", "enforce")
    token = sign_identity_token("usr_real", priv, issuer=ISSUER_LOCAL)
    headers = agent_id_headers(AGENT, user_id="usr_other", identity_token=token)
    assert TestClient(_app()).post("/messages/", headers=headers).status_code == 401


def test_enforce_without_public_key_fails_open(tmp_path, monkeypatch):
    # Deploy misconfiguration (key not mounted) must degrade to audit
    # semantics — mcp is the data plane, not a place to take everyone down.
    monkeypatch.setenv("NX_IDENTITY_PUBLIC_KEY_FILE", str(tmp_path / "missing.pub"))
    monkeypatch.setenv("NX_MCP_AUTH_MODE", "enforce")
    assert TestClient(_app()).post("/messages/").status_code == 200


def test_contextvar_resets_between_requests(tmp_path, monkeypatch):
    priv = _provision(tmp_path, monkeypatch)
    monkeypatch.setenv("NX_MCP_AUTH_MODE", "audit")
    client = TestClient(_app())
    assert client.post("/messages/", headers=_headers(priv)).text == "usr_1"
    assert client.post("/messages/").text == "anon"
    assert verified_caller() is None


# ---------------------------------------------------------------------------
# wiring — every module server wears the middleware
# ---------------------------------------------------------------------------


def test_build_mcp_server_installs_identity_auth_middleware():
    from mcp.server.fastmcp import FastMCP

    from xyz_agent_context.module.module_runner import ModuleRunner

    server = ModuleRunner._build_mcp_server(FastMCP("probe_module"), "probe_module", 7999)
    installed = [m.cls for m in server.config.app.user_middleware]
    assert IdentityAuthMiddleware in installed
