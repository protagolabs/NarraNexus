"""
@file_name: test_auth_error_handling.py
@date: 2026-09-09
@description: The Lark OAuth routes (login/complete/status) had no outer
fallback: an unexpected exception raised anywhere in the handler body
propagated all the way out of the route, and Starlette's default
ServerErrorMiddleware turns an unhandled exception into a plain-text
"Internal Server Error" 500 (GitHub #118 residue; the root-cause #120
NameError itself was already fixed elsewhere). The frontend cannot parse
a plain-text body for an `error` field, so every unrelated crash in this
handler surfaced to the user as a blank/garbled failure instead of the
`{"success": False, "error": ...}` shape every other outcome in this file
already uses.

These tests force an unexpected exception (not one of the handled "no
credential bound" / ownership-rejected paths) and assert the route still
returns that same structured shape instead of letting the exception
escape.
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from narranexus_plugins.lark_module import routes as lark_routes
from narranexus_plugins.lark_module.routes import (
    AgentRequest,
    AuthCompleteRequest,
    lark_auth_complete,
    lark_auth_login,
    lark_auth_status,
)

AGENT = "agent_lark_test"


def _mock_request() -> SimpleNamespace:
    return SimpleNamespace(state=SimpleNamespace(role="user"))


@pytest.fixture(autouse=True)
def _no_ownership_rejection(monkeypatch):
    """Every test here is about the unexpected-exception path, not the
    ownership check — always let the caller through."""
    monkeypatch.setattr(lark_routes, "_verify_agent_ownership", AsyncMock(return_value=None))
    monkeypatch.setattr(lark_routes, "_get_db", AsyncMock(return_value=object()))


@pytest.mark.asyncio
async def test_login_survives_an_unexpected_exception():
    with patch.object(
        lark_routes,
        "LarkCredentialManager",
        side_effect=RuntimeError("unexpected boom"),
    ):
        result = await lark_auth_login(_mock_request(), AgentRequest(agent_id=AGENT))

    assert isinstance(result, dict)
    assert result["success"] is False
    assert "error" in result
    assert "boom" in result["error"]


@pytest.mark.asyncio
async def test_complete_survives_an_unexpected_exception():
    with patch.object(
        lark_routes,
        "LarkCredentialManager",
        side_effect=RuntimeError("unexpected boom"),
    ):
        result = await lark_auth_complete(
            _mock_request(),
            AuthCompleteRequest(agent_id=AGENT, device_code="abc123"),
        )

    assert isinstance(result, dict)
    assert result["success"] is False
    assert "error" in result
    assert "boom" in result["error"]


@pytest.mark.asyncio
async def test_status_survives_an_unexpected_exception():
    with patch.object(
        lark_routes,
        "LarkCredentialManager",
        side_effect=RuntimeError("unexpected boom"),
    ):
        result = await lark_auth_status(_mock_request(), agent_id=AGENT)

    assert isinstance(result, dict)
    assert result["success"] is False
    assert "error" in result
    assert "boom" in result["error"]
