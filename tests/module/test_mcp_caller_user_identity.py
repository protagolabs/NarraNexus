"""
@file_name: test_mcp_caller_user_identity.py
@author: Bin Liang
@date: 2026-08-04
@description: user_id rides the same caller-identity channel as agent_id (W1).

The remaining half of the shared-server identity problem: ``user_id`` is
also a normal tool parameter the MODEL fills. A wrong fill on job_create is
the worst failure shape we have — the job IS created (success=True), but
under the wrong user, so the owner's Jobs list stays empty forever while
the agent reports success ("假成功").

The user_id discipline (differs from agent_id's in how None is treated):
- placeholder ("user_current", ...) → injected turn owner replaces it
- ``None`` / absent → left alone. Retrieval tools use user_id=None as a
  legitimate "no filter" value; injecting there would silently change
  query semantics.
- mismatching real value → the injected turn owner REPLACES it (2026-10-30).
  It used to be kept "until measured"; the measurement (zero mismatches on
  prod over four days) came in alongside a finding that artifact / skill
  tools build workspace paths from this value, so a kept foreign id wrote
  into another tenant's tree. A tool whose ``user_id`` names an EXTERNAL
  user (an IM platform id) opts out with ``@external_user_id``.
- bearer: user_id is field 5 of the positional record — old 4-field
  bearers must still parse (codex carries identity ONLY via bearer;
  header-only would leave codex a degraded consumer, the #229 lesson).
"""
from __future__ import annotations

import contextlib
from unittest.mock import patch

import pytest

from narranexus.platform.module_system._mcp_identity import (
    AGENT_ID_HEADER,
    BEARER_AGENT_PREFIX,
    USER_ID_HEADER,
    agent_id_headers,
    caller_user_id_from_request,
    external_user_id,
    install_caller_identity,
    is_placeholder_user_id,
    resolve_caller_user_id,
)

AGENT = "agent_39b2b72b823b"
USER = "binliang"
OTHER_USER = "user_someone_else"


class _Headers(dict):
    def get(self, key, default=None):  # noqa: D102
        return super().get(key.lower(), default)


@contextlib.contextmanager
def injected(headers: dict | None):
    from mcp.server.lowlevel.server import request_ctx

    if headers is None:
        token = request_ctx.set(type("Ctx", (), {"request": None})())
    else:
        request = type("Req", (), {"headers": _Headers({k.lower(): v for k, v in headers.items()})})()
        token = request_ctx.set(type("Ctx", (), {"request": request})())
    try:
        yield
    finally:
        request_ctx.reset(token)


# ---------------------------------------------------------------------------
# Placeholder detection
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("value", [
    "user_current", "current_user", "current", "me", "self",
    "user_id", "{user_id}", "<user_id>", "requesting_user",
    "", "   ",
])
def test_user_placeholders_are_recognised(value):
    assert is_placeholder_user_id(value) is True


@pytest.mark.parametrize("value", [USER, "user_abc123", "briefing_demo_03"])
def test_real_user_ids_are_not_placeholders(value):
    assert is_placeholder_user_id(value) is False


def test_none_is_not_a_user_placeholder():
    """None is a legitimate 'no filter' value — never treated as a guess."""
    assert is_placeholder_user_id(None) is False


# ---------------------------------------------------------------------------
# Reading the injected user
# ---------------------------------------------------------------------------


def test_user_read_from_explicit_header():
    with injected({USER_ID_HEADER: USER}):
        assert caller_user_id_from_request() == USER


def test_user_read_from_bearer_field_five():
    bearer = f"Bearer {BEARER_AGENT_PREFIX}{AGENT}~chat~~~{USER}"
    with injected({"Authorization": bearer}):
        assert caller_user_id_from_request() == USER


def test_old_four_field_bearer_still_parses_as_no_user():
    bearer = f"Bearer {BEARER_AGENT_PREFIX}{AGENT}~chat"
    with injected({"Authorization": bearer}):
        assert caller_user_id_from_request() is None


def test_headers_builder_emits_user_on_both_channels():
    hdrs = agent_id_headers(AGENT, turn_source="chat", user_id=USER)
    assert hdrs[USER_ID_HEADER] == USER
    assert hdrs["Authorization"].endswith(f"~{USER}")
    with injected(hdrs):
        assert caller_user_id_from_request() == USER


def test_headers_builder_omits_user_when_unknown():
    hdrs = agent_id_headers(AGENT, turn_source="chat")
    assert USER_ID_HEADER not in hdrs
    with injected(hdrs):
        assert caller_user_id_from_request() is None


# ---------------------------------------------------------------------------
# Resolution: placeholder → inject; None → untouched; mismatch → injected wins
# ---------------------------------------------------------------------------


def test_placeholder_user_is_replaced_by_injected():
    with injected({USER_ID_HEADER: USER}):
        assert resolve_caller_user_id("user_current") == USER


def test_none_user_is_left_alone_even_with_injection():
    with injected({USER_ID_HEADER: USER}):
        assert resolve_caller_user_id(None) is None


def test_none_user_does_not_read_request_headers():
    with patch(
        "narranexus.platform.module_system._mcp_identity.caller_user_id_from_request"
    ) as read_injected:
        assert resolve_caller_user_id(None) is None
    read_injected.assert_not_called()


def test_mismatching_user_is_replaced_by_injected():
    with injected({USER_ID_HEADER: USER}):
        assert resolve_caller_user_id(OTHER_USER) == USER


def test_without_injection_everything_passes_through():
    assert resolve_caller_user_id("user_current") == "user_current"
    assert resolve_caller_user_id(USER) == USER


# ---------------------------------------------------------------------------
# Wrapped tools resolve user_id before the body runs
# ---------------------------------------------------------------------------


def _server():
    from mcp.server.fastmcp import FastMCP

    mcp = FastMCP("user-identity-test")

    @mcp.tool()
    async def needs_both(agent_id: str, user_id: str, title: str) -> dict:
        return {"agent_id": agent_id, "user_id": user_id, "title": title}

    @mcp.tool()
    async def optional_user_filter(agent_id: str, user_id: str | None = None) -> dict:
        return {"agent_id": agent_id, "user_id": user_id}

    @mcp.tool()
    @external_user_id
    async def dm_external(agent_id: str, user_id: str, text: str) -> dict:
        return {"agent_id": agent_id, "user_id": user_id}

    install_caller_identity(mcp)
    return mcp


def _fn(mcp, name):
    return {t.name: t for t in mcp._tool_manager.list_tools()}[name].fn


@pytest.mark.asyncio
async def test_wrapped_tool_replaces_placeholder_user():
    fn = _fn(_server(), "needs_both")
    with injected(agent_id_headers(AGENT, user_id=USER)):
        out = await fn(agent_id="agent_current", user_id="user_current", title="t")
    assert out == {"agent_id": AGENT, "user_id": USER, "title": "t"}


@pytest.mark.asyncio
async def test_wrapped_tool_keeps_explicit_none_filter():
    """user_id=None means 'unfiltered query' — injection must not scope it."""
    fn = _fn(_server(), "optional_user_filter")
    with injected(agent_id_headers(AGENT, user_id=USER)):
        out = await fn(agent_id=AGENT)
    assert out == {"agent_id": AGENT, "user_id": None}


@pytest.mark.asyncio
async def test_wrapped_tool_overrides_mismatching_real_user():
    fn = _fn(_server(), "needs_both")
    with injected(agent_id_headers(AGENT, user_id=USER)):
        out = await fn(agent_id=AGENT, user_id=OTHER_USER, title="t")
    assert out["user_id"] == USER


@pytest.mark.asyncio
@pytest.mark.parametrize("external", ["1234567890123456789", "user_current", OTHER_USER])
async def test_external_user_id_tool_is_left_alone(external):
    """An IM platform id is not our account id: neither the placeholder rule
    nor the override may touch it. agent_id is still resolved."""
    fn = _fn(_server(), "dm_external")
    with injected(agent_id_headers(AGENT, user_id=USER)):
        out = await fn(agent_id="agent_current", user_id=external, text="hi")
    assert out == {"agent_id": AGENT, "user_id": external}


# ---------------------------------------------------------------------------
# Inventory gate: every real tool's user_id is classified (2026-10-30)
# ---------------------------------------------------------------------------
# The default is "account id, follows the turn owner". A new tool whose
# user_id is a third-party id and forgets @external_user_id would have its
# argument silently rewritten to the owner — no error, just the wrong target.
# Adding such a tool must turn this red until it is consciously classified.

EXTERNAL_USER_ID_TOOLS = {"discord_dm"}


def _real_user_id_tools():
    import inspect

    from narranexus.platform.module_system import module_registry

    found = {}
    for _name, cls in module_registry.items():
        try:
            mcp = cls(agent_id="probe", user_id="probe", database_client=None).build_instrumented_mcp_server()
        except Exception:
            continue
        if mcp is None:
            continue
        for tool in mcp._tool_manager.list_tools():
            fn = getattr(tool, "fn", None)
            try:
                params = inspect.signature(fn).parameters
            except (TypeError, ValueError):
                continue
            if "user_id" in params:
                found[tool.name] = fn
    return found


def test_every_user_id_tool_is_classified():
    tools = _real_user_id_tools()
    external = {n for n, fn in tools.items() if getattr(fn, "_nx_external_user_id", False)}
    assert external == EXTERNAL_USER_ID_TOOLS, (
        f"tools marked @external_user_id changed: {sorted(external)}. A user_id that "
        f"is NOT a NarraNexus account must be marked; update EXTERNAL_USER_ID_TOOLS "
        f"deliberately."
    )
    # Coverage floor (see test_every_agent_id_tool_is_async): the except/continue
    # above would otherwise let this gate hollow out while staying green.
    assert len(tools) >= 38, f"only {len(tools)} user_id tools inspected — coverage shrank"


@pytest.mark.asyncio
async def test_discord_dm_receives_the_snowflake_through_the_real_wrapper(monkeypatch):
    """Through the real module server (install_caller_identity applied): the
    Discord snowflake must reach the body untouched, never the turn owner."""
    from narranexus_plugins.discord_module import _discord_mcp_tools as tools_mod

    seen = {}

    class _Cred:
        bot_token = "t"

    async def _cred(agent_id):
        return _Cred()

    class _Client:
        def __init__(self, token):
            pass

        async def create_dm_channel(self, user_id):
            seen["user_id"] = user_id
            return ""  # stop before any send

        async def close(self):
            pass

    monkeypatch.setattr(tools_mod, "_get_credential", _cred)
    monkeypatch.setattr(tools_mod, "DiscordSDKClient", _Client)
    fn = _real_user_id_tools()["discord_dm"]
    snowflake = "123456789012345678"
    with injected(agent_id_headers(AGENT, user_id=USER)):
        await fn(agent_id=AGENT, user_id=snowflake, text="hi")
    assert seen["user_id"] == snowflake


@pytest.mark.asyncio
async def test_positional_mismatching_user_is_overridden():
    fn = _fn(_server(), "needs_both")
    with injected(agent_id_headers(AGENT, user_id=USER)):
        out = await fn(AGENT, OTHER_USER, "t")
    assert out["user_id"] == USER


def test_sync_tool_mismatching_user_is_overridden():
    from mcp.server.fastmcp import FastMCP

    mcp = FastMCP("sync-user")

    @mcp.tool()
    def user_only(user_id: str) -> dict:
        return {"user_id": user_id}

    install_caller_identity(mcp)
    with injected({USER_ID_HEADER: USER}):
        assert _fn(mcp, "user_only")(user_id=OTHER_USER) == {"user_id": USER}
