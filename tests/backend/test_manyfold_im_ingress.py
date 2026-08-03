"""
@file_name: test_manyfold_im_ingress.py
@author: NexusAgent
@date: 2026-08-03
@description: Managed-IM inbound dispatch — build_inbound_run_context mapping
(channel_provider/channel_context → WorkingSource + ChannelTag + extra_data)
and the /v1/chat/completions wiring that hands the mapped context to
BackgroundRun. Design: specs/2026-08-03-manyfold-managed-im-ingress-design.md.
"""

import asyncio

import httpx
import pytest
from fastapi import FastAPI, Request
from httpx import ASGITransport

import backend.routes.manyfold.sync as sync_mod
import backend.routes.openai_compat as compat_mod
from xyz_agent_context.schema.channel_tag import ChannelTag
from xyz_agent_context.schema.hook_schema import WorkingSource


# ---------------------------------------------------------------------------
# build_inbound_run_context — provider mapping
# ---------------------------------------------------------------------------


def _plain_expected(user_input: str, session_id: str):
    return (
        WorkingSource.MANYFOLD,
        user_input,
        {"trigger_id": session_id, "retrieval_anchor": user_input},
    )


def test_no_provider_is_plain_manyfold_turn():
    got = sync_mod.build_inbound_run_context(
        channel_provider=None,
        channel_context=None,
        user_input="hello",
        session_id="manyfold_ab12cd34",
    )
    assert got == _plain_expected("hello", "manyfold_ab12cd34")


@pytest.mark.parametrize("provider", ["", "matrix", "fake", "unknown-thing"])
def test_unknown_provider_falls_back_to_manyfold(provider):
    got = sync_mod.build_inbound_run_context(
        channel_provider=provider,
        channel_context={"room_id": "r1"},
        user_input="hi",
        session_id="s1",
    )
    assert got == _plain_expected("hi", "s1")


@pytest.mark.parametrize(
    "provider,expected",
    [
        ("lark", WorkingSource.LARK),
        ("slack", WorkingSource.SLACK),
        ("telegram", WorkingSource.TELEGRAM),
        ("discord", WorkingSource.DISCORD),
        ("wechat", WorkingSource.WECHAT),
        ("narramessenger", WorkingSource.NARRAMESSENGER),
    ],
)
def test_known_providers_map_to_working_source(provider, expected):
    ws, _, _ = sync_mod.build_inbound_run_context(
        channel_provider=provider,
        channel_context={},
        user_input="m",
        session_id="s1",
    )
    assert ws is expected


def test_provider_matching_is_case_and_whitespace_insensitive():
    ws, _, _ = sync_mod.build_inbound_run_context(
        channel_provider="  Lark ",
        channel_context={},
        user_input="m",
        session_id="s1",
    )
    assert ws is WorkingSource.LARK


# ---------------------------------------------------------------------------
# build_inbound_run_context — channel turn shape
# ---------------------------------------------------------------------------


def _lark_context():
    return {
        "room_id": "oc_room1",
        "sender_id": "ou_sender1",
        "sender_name": "Alice",
        "source_message_id": "om_msg1",
    }


def test_channel_turn_prefixes_input_with_channel_tag():
    _, run_input, _ = sync_mod.build_inbound_run_context(
        channel_provider="lark",
        channel_context=_lark_context(),
        user_input="what's up",
        session_id="s1",
    )
    tag = ChannelTag(
        channel="lark",
        sender_name="Alice",
        sender_id="ou_sender1",
        room_id="oc_room1",
    )
    assert run_input == f"{tag.format()}\nwhat's up"


def test_channel_turn_extra_data_shape():
    _, _, extra = sync_mod.build_inbound_run_context(
        channel_provider="lark",
        channel_context=_lark_context(),
        user_input="what's up",
        session_id="s1",
    )
    assert extra["channel_tag"] == {
        "channel": "lark",
        "sender_name": "Alice",
        "sender_id": "ou_sender1",
        "room_id": "oc_room1",
    }
    # Native channel triggers anchor retrieval on "[From <name>] <body>".
    assert extra["retrieval_anchor"] == "[From Alice] what's up"
    # trigger_id follows the native f"{channel}_{message_id}" convention.
    assert extra["trigger_id"] == "lark_om_msg1"
    assert extra["source_message_id"] == "om_msg1"


def test_trigger_id_falls_back_to_session_id_without_message_id():
    ctx = _lark_context()
    ctx["source_message_id"] = None
    _, _, extra = sync_mod.build_inbound_run_context(
        channel_provider="lark",
        channel_context=ctx,
        user_input="m",
        session_id="manyfold_zz99",
    )
    assert extra["trigger_id"] == "manyfold_zz99"
    assert extra["source_message_id"] == ""


def test_sender_name_falls_back_to_sender_id_then_user():
    _, run_input, _ = sync_mod.build_inbound_run_context(
        channel_provider="wechat",
        channel_context={"room_id": "wx1", "sender_id": "wx1", "sender_name": None},
        user_input="m",
        session_id="s1",
    )
    assert "wx1" in run_input.splitlines()[0]

    _, run_input2, extra2 = sync_mod.build_inbound_run_context(
        channel_provider="wechat",
        channel_context={},
        user_input="m",
        session_id="s1",
    )
    assert extra2["channel_tag"]["sender_name"] == "user"
    assert run_input2.splitlines()[0].startswith("[Wechat · user")


def test_optional_fields_only_present_when_provided():
    _, _, bare = sync_mod.build_inbound_run_context(
        channel_provider="lark",
        channel_context=_lark_context(),
        user_input="m",
        session_id="s1",
    )
    for key in ("chat_type", "thread_id", "reply_token", "is_mention"):
        assert key not in bare
    assert "manyfold_attachments" not in bare

    ctx = _lark_context() | {
        "chat_type": "group",
        "thread_id": "t1",
        "reply_token": "ctx_tok",
        "is_mention": False,
        "attachments": [{"name": "a.png", "mime": "image/png", "size": 10, "path": "p"}],
    }
    _, _, full = sync_mod.build_inbound_run_context(
        channel_provider="lark",
        channel_context=ctx,
        user_input="m",
        session_id="s1",
    )
    assert full["chat_type"] == "group"
    assert full["thread_id"] == "t1"
    assert full["reply_token"] == "ctx_tok"
    assert full["is_mention"] is False
    assert full["manyfold_attachments"] == ctx["attachments"]


def test_context_values_are_coerced_and_junk_tolerated():
    _, _, extra = sync_mod.build_inbound_run_context(
        channel_provider="telegram",
        channel_context={
            "room_id": -100123,
            "sender_id": 42,
            "source_message_id": 7,
            "thread_id": 9,
            "attachments": "not-a-list",
        },
        user_input="m",
        session_id="s1",
    )
    assert extra["channel_tag"]["room_id"] == "-100123"
    assert extra["channel_tag"]["sender_id"] == "42"
    assert extra["trigger_id"] == "telegram_7"
    assert extra["thread_id"] == "9"
    assert "manyfold_attachments" not in extra


def test_non_dict_context_is_treated_as_empty():
    ws, run_input, extra = sync_mod.build_inbound_run_context(
        channel_provider="discord",
        channel_context="garbage",  # type: ignore[arg-type]
        user_input="m",
        session_id="s1",
    )
    assert ws is WorkingSource.DISCORD
    assert extra["channel_tag"]["sender_name"] == "user"
    assert run_input.endswith("\nm")


# ---------------------------------------------------------------------------
# /v1/chat/completions wiring — mapped context reaches BackgroundRun.drive
# ---------------------------------------------------------------------------


class _FakeBroadcaster:
    def subscribe(self, session_id):
        async def _empty():
            return
            yield  # pragma: no cover

        return _empty()

    def unsubscribe(self, session_id):
        pass


class _FakeBackgroundRun:
    instances: list = []

    def __init__(self, **kwargs):
        self.init_kwargs = kwargs
        self.drive_kwargs = None
        self.task = None
        self.ready_event = asyncio.Event()
        self.ready_event.set()
        self.broadcaster = _FakeBroadcaster()
        _FakeBackgroundRun.instances.append(self)

    async def drive(self, **kwargs):
        self.drive_kwargs = kwargs


@pytest.fixture
def compat_app(monkeypatch):
    _FakeBackgroundRun.instances = []

    async def fake_creator(agent_id):
        return "user_1"

    async def fake_db():
        return object()

    monkeypatch.setattr(compat_mod, "_resolve_agent_creator", fake_creator)
    monkeypatch.setattr(compat_mod, "get_db_client", fake_db)
    monkeypatch.setattr(compat_mod, "BackgroundRun", _FakeBackgroundRun)

    app = FastAPI()

    @app.middleware("http")
    async def _authed(request: Request, call_next):
        request.state.manyfold_authed = True
        return await call_next(request)

    app.include_router(compat_mod.router)
    app.state.active_runs = {}
    return app


async def _post_completions(app, body):
    transport = ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as client:
        return await client.post("/v1/chat/completions", json=body)


async def test_endpoint_plain_turn_stays_manyfold(compat_app):
    resp = await _post_completions(
        compat_app,
        {
            "model": "agent_x",
            "messages": [{"role": "user", "content": "hello"}],
            "stream": False,
        },
    )
    assert resp.status_code == 200
    run = _FakeBackgroundRun.instances[-1]
    assert run.drive_kwargs["working_source"] is WorkingSource.MANYFOLD
    assert run.drive_kwargs["input_content"] == "hello"
    assert run.drive_kwargs["trigger_extra_data"]["retrieval_anchor"] == "hello"


async def test_endpoint_channel_turn_dispatches_working_source(compat_app):
    resp = await _post_completions(
        compat_app,
        {
            "model": "agent_x",
            "messages": [{"role": "user", "content": "hi there"}],
            "stream": False,
            "channel_provider": "lark",
            "channel_context": _lark_context(),
        },
    )
    assert resp.status_code == 200
    run = _FakeBackgroundRun.instances[-1]
    assert run.drive_kwargs["working_source"] is WorkingSource.LARK
    assert run.drive_kwargs["input_content"].startswith("[Lark · Alice")
    extra = run.drive_kwargs["trigger_extra_data"]
    assert extra["channel_tag"]["room_id"] == "oc_room1"
    assert extra["trigger_id"] == "lark_om_msg1"


async def test_endpoint_unknown_channel_provider_is_ignored(compat_app):
    resp = await _post_completions(
        compat_app,
        {
            "model": "agent_x",
            "messages": [{"role": "user", "content": "hi"}],
            "stream": False,
            "channel_provider": "matrix",
            "channel_context": {"room_id": "!r:hs"},
        },
    )
    assert resp.status_code == 200
    run = _FakeBackgroundRun.instances[-1]
    assert run.drive_kwargs["working_source"] is WorkingSource.MANYFOLD
    assert run.drive_kwargs["input_content"] == "hi"


# ---------------------------------------------------------------------------
# Stage B — reply classification via MessageSourceRegistry declaration chain
# ---------------------------------------------------------------------------

from xyz_agent_context.channel.message_source_handler import (  # noqa: E402
    MessageSourceHandler,
    MessageSourceRegistry,
)

_DEFAULT = MessageSourceRegistry.get("nonexistent-source")


def _tool_event(tool_name, arguments):
    return {
        "type": "progress",
        "details": {"tool_name": tool_name, "arguments": arguments},
    }


def test_classify_owner_chat_reply_tool_is_content():
    kind, payload = compat_mod._classify_event(
        _tool_event("mcp__chat_module__send_message_to_user_directly", {"content": "hi"}),
        _DEFAULT,
    )
    assert (kind, payload) == ("content", "hi")


def test_classify_other_tool_is_tool_call_under_default_handler():
    kind, payload = compat_mod._classify_event(
        _tool_event("lark_cli", {"command": "im +messages-send --chat-id c --markdown yo"}),
        _DEFAULT,
    )
    assert kind == "tool_call"


def test_classify_uses_channel_extractor_for_send_and_tool_call_for_rest():
    def _extract(tool_name, args):
        if "cli" in tool_name and "--markdown" in args.get("command", ""):
            return args["command"].split("--markdown ", 1)[1]
        return None

    handler = MessageSourceHandler(
        name="larkish",
        user_reply_tool_names=("lark_cli",),
        extract_reply_fn=_extract,
    )
    kind, payload = compat_mod._classify_event(
        _tool_event("mcp__lark_module__lark_cli", {"command": "im +messages-send --markdown hello!"}),
        handler,
    )
    assert (kind, payload) == ("content", "hello!")

    kind2, _ = compat_mod._classify_event(
        _tool_event("mcp__lark_module__lark_cli", {"command": "im +chat-messages-list"}),
        handler,
    )
    assert kind2 == "tool_call"


def test_classify_non_tool_channels_unchanged_by_handler():
    assert compat_mod._classify_event(
        {"type": "agent_thinking", "thinking_content": "mm"}, _DEFAULT
    ) == ("reasoning", "mm")
    assert compat_mod._classify_event(
        {"type": "agent_response", "delta": "tok"}, _DEFAULT
    ) == ("reasoning", "tok")
    assert compat_mod._classify_event(
        {"type": "agent_tool_output", "details": {"output": "res"}}, _DEFAULT
    ) == ("tool_result", "res")


async def test_stream_fallback_is_neutral_for_channel_turn(compat_app):
    resp = await _post_completions(
        compat_app,
        {
            "model": "agent_x",
            "messages": [{"role": "user", "content": "hi"}],
            "stream": True,
            "channel_provider": "lark",
            "channel_context": _lark_context(),
        },
    )
    body = resp.text
    assert "channel turn completed" in body
    assert "produced no user-visible reply" not in body
    assert "data: [DONE]" in body


async def test_stream_fallback_keeps_legacy_text_for_plain_turn(compat_app):
    resp = await _post_completions(
        compat_app,
        {
            "model": "agent_x",
            "messages": [{"role": "user", "content": "hi"}],
            "stream": True,
        },
    )
    assert "produced no user-visible reply" in resp.text
