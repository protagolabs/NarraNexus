"""
@file_name: test_auth_401_codes.py
@author: Bin Liang
@date: 2026-08-06
@description: Every 401 the backend emits must carry a machine-readable
``code`` and must be logged. Without this the frontend has only the HTTP
status to go on, so it treats a NetMind-token rejection or an internal
identity-resolution bug exactly like a dead session and nukes the whole
SPA session (0802 incident: users bounced to /login mid-demo).

Two invariants are pinned here:

1. **Discriminated 401s.** Session-death (expired / invalid / missing
   token) is a different `code` from everything else, and only the
   session-death set is in ``SESSION_DEAD_CODES``.
2. **Observable 401s.** Middleware rejections used to return a bare
   JSONResponse with no logger call at all, which is why the 8/2
   incident could not be diagnosed from server logs. Every rejection
   now logs path + code.

Plus the ``GET /api/auth/session`` probe the frontend uses to confirm a
session is really dead before destroying it.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import jwt
import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient
from loguru import logger

from backend import auth as auth_mod
from backend.auth import JWT_ALGORITHM, JWT_SECRET, auth_middleware, create_token
from backend.auth_errors import (
    GATEWAY_TOKEN_INVALID,
    IDENTITY_MISSING,
    IDENTITY_UNRESOLVED,
    NETMIND_TOKEN_INVALID,
    SESSION_DEAD_CODES,
    TOKEN_EXPIRED,
    TOKEN_INVALID,
    TOKEN_MISSING,
    AuthError,
    install_auth_error_handler,
)


@pytest.fixture
def log_lines():
    """Capture loguru output for the duration of a test."""
    lines: list[str] = []
    sink_id = logger.add(lambda m: lines.append(str(m)), level="DEBUG")
    yield lines
    logger.remove(sink_id)


def _build_app() -> FastAPI:
    app = FastAPI()
    app.middleware("http")(auth_middleware)
    install_auth_error_handler(app)

    @app.get("/api/agents")
    async def list_agents():
        return {"ok": True}

    # Stands in for the route-level guards (providers._get_user_id,
    # notifications, auth._require_request_user): auth passed, but the
    # handler could not resolve an identity. NOT session death.
    @app.get("/api/providers")
    async def providers(request: Request):
        raise AuthError(IDENTITY_UNRESOLVED, "Authentication required")

    # Stands in for the NetMind-token endpoints under /api/providers and
    # /api/billing: a second, unrelated credential failed.
    @app.get("/api/providers/netmind/keys")
    async def netmind_keys():
        raise AuthError(NETMIND_TOKEN_INVALID, "NetMind token invalid or expired")

    return app


@pytest.fixture
def cloud_client(monkeypatch):
    monkeypatch.setattr(auth_mod, "_is_cloud_mode", lambda: True)
    return TestClient(_build_app())


@pytest.fixture
def local_client(monkeypatch):
    monkeypatch.setattr(auth_mod, "_is_cloud_mode", lambda: False)
    return TestClient(_build_app())


def _expired_token(user_id: str = "alice") -> str:
    now = datetime.now(timezone.utc)
    return jwt.encode(
        {"user_id": user_id, "role": "user", "iat": now - timedelta(days=8),
         "exp": now - timedelta(days=1)},
        JWT_SECRET,
        algorithm=JWT_ALGORITHM,
    )


# ---------------------------------------------------------------------------
# 1. Session-death codes
# ---------------------------------------------------------------------------

def test_expired_token_is_tagged_token_expired(cloud_client):
    resp = cloud_client.get(
        "/api/agents", headers={"Authorization": f"Bearer {_expired_token()}"}
    )
    assert resp.status_code == 401
    assert resp.json()["code"] == TOKEN_EXPIRED
    assert TOKEN_EXPIRED in SESSION_DEAD_CODES


def test_garbage_token_is_tagged_token_invalid(cloud_client):
    resp = cloud_client.get(
        "/api/agents", headers={"Authorization": "Bearer not-a-jwt"}
    )
    assert resp.status_code == 401
    assert resp.json()["code"] == TOKEN_INVALID
    assert TOKEN_INVALID in SESSION_DEAD_CODES


def test_missing_bearer_is_tagged_token_missing(cloud_client):
    resp = cloud_client.get("/api/agents")
    assert resp.status_code == 401
    assert resp.json()["code"] == TOKEN_MISSING
    assert TOKEN_MISSING in SESSION_DEAD_CODES


def test_local_mode_missing_user_header_is_tagged_identity_missing(local_client):
    resp = local_client.get("/api/agents")
    assert resp.status_code == 401
    assert resp.json()["code"] == IDENTITY_MISSING
    # Only a re-login repopulates configStore.userId, so this IS session death.
    assert IDENTITY_MISSING in SESSION_DEAD_CODES


# ---------------------------------------------------------------------------
# 2. Non-session-death 401s must be distinguishable
# ---------------------------------------------------------------------------

def test_route_level_identity_failure_is_not_session_death(cloud_client):
    """providers/notifications/analytics raise this when auth passed but
    request.state.user_id is empty — an internal bug, not a dead JWT."""
    headers = {"Authorization": f"Bearer {create_token('alice', 'user')}"}
    resp = cloud_client.get("/api/providers", headers=headers)
    assert resp.status_code == 401
    assert resp.json()["code"] == IDENTITY_UNRESOLVED
    assert IDENTITY_UNRESOLVED not in SESSION_DEAD_CODES


def test_netmind_token_failure_is_not_session_death(cloud_client):
    """The 8/2 log's `/api/providers` 401: the user's NarraNexus session was
    fine, only the NetMind credential was stale. Logging them out was wrong."""
    headers = {"Authorization": f"Bearer {create_token('alice', 'user')}"}
    resp = cloud_client.get("/api/providers/netmind/keys", headers=headers)
    assert resp.status_code == 401
    assert resp.json()["code"] == NETMIND_TOKEN_INVALID
    assert NETMIND_TOKEN_INVALID not in SESSION_DEAD_CODES


def test_gateway_token_code_is_not_session_death():
    """Manyfold gateway rejections must never log a user out of the UI."""
    assert GATEWAY_TOKEN_INVALID not in SESSION_DEAD_CODES


def test_detail_stays_a_plain_string(cloud_client):
    """`code` is additive — the frontend's existing `detail` extraction (and
    every human reading a log) must keep working unchanged."""
    resp = cloud_client.get("/api/agents")
    assert isinstance(resp.json()["detail"], str)


# ---------------------------------------------------------------------------
# 3. Observability — the gap that made 8/2 undiagnosable
# ---------------------------------------------------------------------------

def test_middleware_rejection_is_logged_with_path_and_code(cloud_client, log_lines):
    cloud_client.get(
        "/api/agents", headers={"Authorization": f"Bearer {_expired_token()}"}
    )
    hits = [ln for ln in log_lines if "[auth-reject]" in ln]
    assert hits, "middleware 401 must emit a log line"
    assert TOKEN_EXPIRED in hits[0]
    assert "/api/agents" in hits[0]


def test_expired_token_log_carries_token_lifetime(cloud_client, log_lines):
    """Without iat/exp in the log we cannot tell a naturally-expired token
    from a token signed by a different key — the exact question 8/2 left open."""
    cloud_client.get(
        "/api/agents", headers={"Authorization": f"Bearer {_expired_token()}"}
    )
    hits = [ln for ln in log_lines if "[auth-reject]" in ln]
    assert "iat=" in hits[0] and "exp=" in hits[0]


def test_route_level_rejection_is_logged(cloud_client, log_lines):
    headers = {"Authorization": f"Bearer {create_token('alice', 'user')}"}
    cloud_client.get("/api/providers", headers=headers)
    hits = [ln for ln in log_lines if "[auth-reject]" in ln]
    assert hits and IDENTITY_UNRESOLVED in hits[0]


# ---------------------------------------------------------------------------
# 4. GET /api/auth/session — the probe the frontend uses to confirm a 401
#    really means "your session is dead" before it destroys the session.
# ---------------------------------------------------------------------------

@pytest.fixture
def probe_client(monkeypatch):
    """Real auth router behind the real middleware — the probe must be
    subject to exactly the same JWT check as any other endpoint, otherwise
    it cannot answer the question it exists to answer."""
    monkeypatch.setattr(auth_mod, "_is_cloud_mode", lambda: True)
    import backend.routes.auth as auth_routes

    app = FastAPI()
    app.middleware("http")(auth_middleware)
    install_auth_error_handler(app)
    app.include_router(auth_routes.router, prefix="/api/auth")
    return TestClient(app)


def test_session_probe_confirms_a_live_session(probe_client):
    token = create_token("alice", "user")
    resp = probe_client.get(
        "/api/auth/session", headers={"Authorization": f"Bearer {token}"}
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["user_id"] == "alice"
    # Drives the pre-expiry warning: the frontend needs to know WHEN, not
    # just whether, the session dies.
    assert isinstance(body["expires_at"], int)


def test_session_probe_rejects_a_dead_session(probe_client):
    resp = probe_client.get(
        "/api/auth/session",
        headers={"Authorization": f"Bearer {_expired_token()}"},
    )
    assert resp.status_code == 401
    assert resp.json()["code"] == TOKEN_EXPIRED


def test_session_probe_touches_no_database(probe_client, monkeypatch):
    """The probe runs on every suspected-dead session; it must stay a pure
    token check so a burst of 401s can't turn into a burst of DB queries."""
    import backend.routes.auth as auth_routes

    def _boom():
        raise AssertionError("session probe must not hit the database")

    monkeypatch.setattr(auth_routes, "get_db_client", _boom)
    token = create_token("alice", "user")
    resp = probe_client.get(
        "/api/auth/session", headers={"Authorization": f"Bearer {token}"}
    )
    assert resp.status_code == 200
