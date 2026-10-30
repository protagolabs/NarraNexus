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
# Every other tool whose user_id names the NarraNexus account (turn owner).
# A new user_id tool is in neither set until someone decides which it is.
ACCOUNT_USER_ID_TOOLS = {
    "agent_self", "compare_dates", "create_narrative", "job_create",
    "job_retrieval_by_keywords", "job_retrieval_semantic", "list_artifacts",
    "notify_owner", "open_url", "plugin_activate", "plugin_deactivate",
    "plugin_diff", "plugin_docs", "plugin_edit", "plugin_install", "plugin_list",
    "plugin_observe", "plugin_publish_hint", "plugin_register", "plugin_rollback",
    "plugin_scaffold", "plugin_search", "plugin_test", "plugin_upgrade",
    "plugin_validate", "register_artifact", "reply_owner", "resolve_relative_date",
    "skill_backup_from_github", "skill_backup_from_local_zip", "skill_backup_from_md",
    "skill_install", "skill_list_required_env", "skill_list_unbackedup",
    "skill_save_config", "skill_save_study_summary", "skill_search_marketplace",
    "skill_uninstall", "submit_feedback",
}


def _real_user_id_tools():
    from tests.module._tool_surface import served_tools

    tools, failed = served_tools()
    # A module that cannot be built would hide its tools from the gate.
    assert not failed, f"modules failed to build under probe args: {failed}"
    return {t.name: t.fn for t in tools if "user_id" in t.params}


def test_every_user_id_tool_is_classified():
    """Full classification, not just the marked set: a NEW user_id tool (marked
    or not) is in neither list and turns this red until someone decides whether
    its user_id is our account or a third-party id. Set equality also fails if
    a module stops building (its tools vanish), so the swallowed construction
    errors in _real_user_id_tools cannot hollow the gate out."""
    assert not (ACCOUNT_USER_ID_TOOLS & EXTERNAL_USER_ID_TOOLS)
    tools = _real_user_id_tools()
    unclassified = set(tools) - ACCOUNT_USER_ID_TOOLS - EXTERNAL_USER_ID_TOOLS
    missing = (ACCOUNT_USER_ID_TOOLS | EXTERNAL_USER_ID_TOOLS) - set(tools)
    assert not unclassified, (
        f"new user_id tools {sorted(unclassified)}: add each to ACCOUNT_USER_ID_TOOLS, "
        f"or mark it @external_user_id and add it to EXTERNAL_USER_ID_TOOLS"
    )
    assert not missing, f"classified tools no longer found (module failed to build?): {sorted(missing)}"
    for name, fn in tools.items():
        marked = bool(getattr(fn, "_nx_external_user_id", False))
        assert marked == (name in EXTERNAL_USER_ID_TOOLS), name


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


# ---------------------------------------------------------------------------
# Overrides leave a durable, bounded audit row (auto-review #428, Important)
# ---------------------------------------------------------------------------
# Rewriting user_id silently changes which account a call lands under; a log
# line alone vanishes with the container. Same shape as mcp_auth's tokenless
# measurement: in-process counts, bounded keys, one row per flush window.


@pytest.fixture(autouse=True)
def _clean_override_state():
    """Every test starts and ends with no pending override counts, so no
    test's leftovers get flushed by a later test's tool call."""
    from narranexus.platform.module_system import _mcp_identity as ident

    ident._OVERRIDES.reset()
    yield
    ident._OVERRIDES.reset()


@pytest.fixture
def audit_rows(monkeypatch):
    from narranexus.platform.module_system import _mcp_identity as ident

    rows: list[dict] = []

    async def fake_get_db_client():
        return object()

    class FakeAuditRepo:
        def __init__(self, db):
            pass

        async def record(self, **kw):
            rows.append(kw)

    monkeypatch.setattr("narranexus.platform.utils.db.db_factory.get_db_client", fake_get_db_client)
    monkeypatch.setattr(
        "narranexus.platform.repository.executor_audit_repository.ExecutorAuditRepository",
        FakeAuditRepo,
    )
    ident._OVERRIDES.reset()
    return rows


@pytest.mark.asyncio
async def test_override_is_recorded_as_an_audit_row(audit_rows):
    fn = _fn(_server(), "needs_both")
    with injected(agent_id_headers(AGENT, user_id=USER)):
        await fn(agent_id=AGENT, user_id=OTHER_USER, title="t")
    assert len(audit_rows) == 1
    row = audit_rows[0]
    assert row["event_type"] == "mcp_identity_user_override"
    assert row["detail"]["total"] == 1
    (entry,) = row["detail"]["counts"]
    assert entry == {"tool": "needs_both", "supplied": OTHER_USER, "owner": USER, "n": 1}


@pytest.mark.asyncio
async def test_placeholder_and_matching_values_are_not_overrides(audit_rows):
    fn = _fn(_server(), "needs_both")
    with injected(agent_id_headers(AGENT, user_id=USER)):
        await fn(agent_id=AGENT, user_id="user_current", title="t")
        await fn(agent_id=AGENT, user_id=USER, title="t")
    assert audit_rows == []


def test_override_keys_are_bounded(audit_rows, monkeypatch):
    from narranexus.platform.module_system import _mcp_identity as ident
    from narranexus.platform.module_system._audit_window import OVERFLOW_KEY

    monkeypatch.setattr(ident._OVERRIDES, "_deadline", float("inf"))  # hold the window open
    with injected({USER_ID_HEADER: USER}):
        for i in range(ident._OVERRIDE_KEYS_MAX + 50):
            resolve_caller_user_id(f"forged_user_{i:04d}" + "x" * 200, tool="t")
    pending = ident._OVERRIDES.pending()
    assert len(pending) == ident._OVERRIDE_KEYS_MAX + 1
    assert pending[OVERFLOW_KEY] == 50
    assert all(len(k[1]) <= ident._OVERRIDE_VALUE_MAX for k in pending if k != OVERFLOW_KEY)


@pytest.mark.asyncio
async def test_sync_tool_override_is_flushed_by_the_next_async_call(audit_rows):
    from mcp.server.fastmcp import FastMCP

    mcp = FastMCP("sync-then-async")

    @mcp.tool()
    def user_only(user_id: str) -> dict:
        return {"user_id": user_id}

    @mcp.tool()
    async def needs_both(agent_id: str, user_id: str) -> dict:
        return {}

    install_caller_identity(mcp)
    with injected(agent_id_headers(AGENT, user_id=USER)):
        _fn(mcp, "user_only")(user_id=OTHER_USER)
        assert audit_rows == []  # a sync frame cannot await the write
        await _fn(mcp, "needs_both")(agent_id=AGENT, user_id=USER)
    assert len(audit_rows) == 1
    assert audit_rows[0]["detail"]["counts"][0]["tool"] == "user_only"


@pytest.mark.asyncio
async def test_audit_failure_never_breaks_the_tool(monkeypatch):
    from narranexus.platform.module_system import _mcp_identity as ident

    async def boom():
        raise RuntimeError("db down")

    monkeypatch.setattr("narranexus.platform.utils.db.db_factory.get_db_client", boom)
    ident._OVERRIDES.reset()
    fn = _fn(_server(), "needs_both")
    with injected(agent_id_headers(AGENT, user_id=USER)):
        out = await fn(agent_id=AGENT, user_id=OTHER_USER, title="t")
    assert out["user_id"] == USER


@pytest.mark.asyncio
async def test_overrides_within_one_window_share_one_row(audit_rows):
    """The 60s window coalesces: two overrides back to back -> one row, n=2."""
    from narranexus.platform.module_system import _mcp_identity as ident

    fn = _fn(_server(), "needs_both")
    with injected(agent_id_headers(AGENT, user_id=USER)):
        await fn(agent_id=AGENT, user_id=OTHER_USER, title="t")   # flushes (deadline 0)
        await fn(agent_id=AGENT, user_id=OTHER_USER, title="t")   # counted, window open
        await fn(agent_id=AGENT, user_id=OTHER_USER, title="t")
    assert len(audit_rows) == 1
    assert ident._OVERRIDES.pending()[("needs_both", OTHER_USER, USER)] == 2


@pytest.mark.asyncio
async def test_a_denied_call_still_flushes(audit_rows, monkeypatch):
    """Flush runs before the ownership check, so a refused call still lands
    its pending counts."""
    async def deny(_agent_id):
        return "not your agent"

    monkeypatch.setattr(
        "narranexus.platform.module_system.identity.mcp_auth.check_agent_ownership", deny
    )
    fn = _fn(_server(), "needs_both")
    with injected(agent_id_headers(AGENT, user_id=USER)):
        out = await fn(agent_id=AGENT, user_id=OTHER_USER, title="t")
    assert out == {"success": False, "error": "not your agent"}
    assert len(audit_rows) == 1


def test_audit_rows_name_the_registered_tool_not_the_function():
    """`@mcp.tool(name=...)` may differ from the function name; the audit row
    must carry the name the classification gate and tool lists use."""
    from mcp.server.fastmcp import FastMCP

    from narranexus.platform.module_system import _mcp_identity as ident

    mcp = FastMCP("renamed")

    @mcp.tool(name="public_name")
    def _impl(user_id: str) -> dict:
        return {}

    install_caller_identity(mcp)
    with injected({USER_ID_HEADER: USER}):
        _fn(mcp, "public_name")(user_id=OTHER_USER)
    assert ("public_name", OTHER_USER, USER) in ident._OVERRIDES.pending()
