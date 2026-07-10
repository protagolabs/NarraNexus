"""
@file_name: test_processing_indicator.py
@author: NarraNexus
@date: 2026-07-10
@description: Outcome-aware processing indicator — the platform-native "the
agent is working" signal each IM channel paints while a run is in flight, then
swaps to a done/error terminal.

Covers:
  - ``ProcessingIndicatorHandle`` outcome carrier.
  - The shared ``_emoji_reaction_indicator`` skeleton lifecycle (working →
    done/error), best-effort swallowing of reaction failures, and marking
    error when the wrapped body raises.
  - The base seam wiring: ``_build_and_run_agent`` marks the handle with the
    run outcome (clean / is_error / raised) before the indicator tears down.
  - Per-channel overrides: Lark / Slack / Discord paint their native emoji and
    swap on success vs error; WeChat inherits the no-op default (iLink v1 has
    no typing/reaction capability); a missing message id short-circuits.
"""
from __future__ import annotations

from contextlib import asynccontextmanager
from dataclasses import dataclass, field

import pytest

from xyz_agent_context.channel.channel_trigger_base import ProcessingIndicatorHandle
from xyz_agent_context.module.lark_module.lark_cli_client import _extract_reaction_id
from xyz_agent_context.schema.parsed_message import ParsedMessage
from tests.channel.test_mock_channel_trigger_integration import (
    _FakeCredential,
    _FakeTrigger,
)


def _msg(mid: str = "om_1") -> ParsedMessage:
    return ParsedMessage(
        message_id=mid, chat_id="C1", sender_id="u1",
        sender_name="Alice", content="hi", timestamp_ms=1,
    )


# ── ProcessingIndicatorHandle ────────────────────────────────────────


def test_handle_defaults_to_success():
    assert ProcessingIndicatorHandle().errored is False


def test_handle_set_error_toggles():
    h = ProcessingIndicatorHandle()
    h.set_error(True)
    assert h.errored is True
    h.set_error(False)
    assert h.errored is False


# ── _emoji_reaction_indicator skeleton ───────────────────────────────


class _ReactionRecorder(_FakeTrigger):
    """Drives the shared skeleton with recording add/remove callables."""

    def __init__(self, *, add_raises: bool = False, remove_raises: bool = False):
        super().__init__([], _FakeCredential())
        self.calls: list[tuple[str, object]] = []
        self._add_raises = add_raises
        self._remove_raises = remove_raises

    @asynccontextmanager
    async def run(self):
        async def _add(emoji: str):
            self.calls.append(("add", emoji))
            if self._add_raises:
                raise RuntimeError("add boom")
            return f"rid_{emoji}"

        async def _remove(token, emoji):
            self.calls.append(("remove", token))
            if self._remove_raises:
                raise RuntimeError("remove boom")

        async with self._emoji_reaction_indicator(
            add=_add, remove=_remove, working="W", done="D", error="E",
        ) as handle:
            yield handle


@pytest.mark.asyncio
async def test_reaction_success_swaps_working_to_done():
    t = _ReactionRecorder()
    async with t.run():
        pass
    assert t.calls == [("add", "W"), ("remove", "rid_W"), ("add", "D")]


@pytest.mark.asyncio
async def test_reaction_error_swaps_working_to_error():
    t = _ReactionRecorder()
    async with t.run() as handle:
        handle.set_error(True)
    assert t.calls == [("add", "W"), ("remove", "rid_W"), ("add", "E")]


@pytest.mark.asyncio
async def test_reaction_body_exception_marks_error_and_propagates():
    t = _ReactionRecorder()
    with pytest.raises(ValueError):
        async with t.run():
            raise ValueError("body boom")
    assert t.calls == [("add", "W"), ("remove", "rid_W"), ("add", "E")]


@pytest.mark.asyncio
async def test_reaction_add_failure_is_swallowed():
    t = _ReactionRecorder(add_raises=True)
    async with t.run():  # must not raise
        pass
    # working add attempted (failed → no token), removal called with None,
    # terminal add attempted (also fails, swallowed). No exception escapes.
    assert t.calls[0] == ("add", "W")
    assert ("remove", None) in t.calls


@pytest.mark.asyncio
async def test_reaction_remove_failure_is_swallowed():
    t = _ReactionRecorder(remove_raises=True)
    async with t.run():  # must not raise
        pass
    assert ("add", "D") in t.calls  # terminal still added despite remove fail


# ── base seam: _build_and_run_agent marks the outcome ────────────────


@dataclass
class _StubResult:
    output_text: str = "ok"
    is_error: bool = False
    error: object = None
    raw_items: list = field(default_factory=list)


@dataclass
class _StubError:
    error_type: str = "BoomError"
    error_message: str = "boom"


class _OutcomeTrigger(_FakeTrigger):
    """Records the outcome the base seam stamps on the yielded handle."""

    def __init__(self):
        super().__init__([], _FakeCredential(agent_id="agent_a"))
        self.outcomes: list[bool] = []

    @asynccontextmanager
    async def processing_indicator(self, credential, message):
        handle = ProcessingIndicatorHandle()
        try:
            yield handle
        finally:
            self.outcomes.append(handle.errored)


async def _drive(trigger, db_client, monkeypatch, *, result=None, raises=None):
    import xyz_agent_context.agent_runtime.agent_runtime as ar_mod
    import xyz_agent_context.agent_runtime.run_collector as rc_mod

    async def _fake_collect_run(*_a, **_k):
        if raises is not None:
            raise raises
        return result

    class _FakeAgentRuntime:
        def __init__(self, *a, **k):
            pass

    monkeypatch.setattr(ar_mod, "AgentRuntime", _FakeAgentRuntime)
    monkeypatch.setattr(rc_mod, "collect_run", _fake_collect_run)
    trigger._db = db_client
    return await trigger._build_and_run_agent(
        trigger._credential, _msg(), "Alice", attachments=[]
    )


@pytest.mark.asyncio
async def test_seam_marks_success_on_clean_run(db_client, monkeypatch):
    t = _OutcomeTrigger()
    await _drive(t, db_client, monkeypatch, result=_StubResult(is_error=False))
    assert t.outcomes == [False]


@pytest.mark.asyncio
async def test_seam_marks_error_on_is_error(db_client, monkeypatch):
    t = _OutcomeTrigger()
    await _drive(
        t, db_client, monkeypatch,
        result=_StubResult(is_error=True, error=_StubError()),
    )
    assert t.outcomes == [True]


@pytest.mark.asyncio
async def test_seam_marks_error_on_raise(db_client, monkeypatch):
    t = _OutcomeTrigger()
    await _drive(t, db_client, monkeypatch, raises=RuntimeError("boom"))
    assert t.outcomes == [True]


# ── _extract_reaction_id (Lark payload parsing) ──────────────────────


def test_extract_reaction_id_direct_and_wrapped():
    assert _extract_reaction_id({"reaction_id": "r1"}) == "r1"
    assert _extract_reaction_id({"data": {"reaction_id": "r2"}}) == "r2"
    assert _extract_reaction_id({}) == ""
    assert _extract_reaction_id(None) == ""


# ── per-channel overrides ────────────────────────────────────────────


class _FakeLarkCLI:
    def __init__(self, *, fail_add: bool = False):
        self.added: list[tuple[str, str]] = []
        self.removed: list[tuple[str, str]] = []
        self.fail_add = fail_add

    async def add_reaction(self, agent_id, message_id, emoji_type):
        self.added.append((message_id, emoji_type))
        if self.fail_add:
            raise RuntimeError("no reaction scope")
        return f"rid_{emoji_type}"

    async def remove_reaction(self, agent_id, message_id, reaction_id):
        self.removed.append((message_id, reaction_id))


@dataclass
class _LarkCred:
    agent_id: str = "agent_a"


@pytest.mark.asyncio
async def test_lark_indicator_success_typing_to_done():
    from xyz_agent_context.module.lark_module.lark_trigger import LarkTrigger

    t = LarkTrigger()
    cli = _FakeLarkCLI()
    t._cli = cli
    async with t.processing_indicator(_LarkCred(), _msg("om_x")):
        pass
    assert cli.added == [("om_x", "Typing"), ("om_x", "DONE")]
    assert cli.removed == [("om_x", "rid_Typing")]


@pytest.mark.asyncio
async def test_lark_indicator_error_typing_to_error():
    from xyz_agent_context.module.lark_module.lark_trigger import LarkTrigger

    t = LarkTrigger()
    cli = _FakeLarkCLI()
    t._cli = cli
    async with t.processing_indicator(_LarkCred(), _msg("om_x")) as handle:
        handle.set_error(True)
    assert cli.added == [("om_x", "Typing"), ("om_x", "ERROR")]


@pytest.mark.asyncio
async def test_lark_indicator_missing_message_id_no_reaction():
    from xyz_agent_context.module.lark_module.lark_trigger import LarkTrigger

    t = LarkTrigger()
    cli = _FakeLarkCLI()
    t._cli = cli
    async with t.processing_indicator(_LarkCred(), _msg("")):
        pass
    assert cli.added == []
    assert cli.removed == []


@pytest.mark.asyncio
async def test_lark_indicator_add_failure_never_aborts():
    from xyz_agent_context.module.lark_module.lark_trigger import LarkTrigger

    t = LarkTrigger()
    cli = _FakeLarkCLI(fail_add=True)
    t._cli = cli
    # Missing scope raises inside add_reaction; the skeleton swallows it and
    # the body still runs to completion.
    ran = False
    async with t.processing_indicator(_LarkCred(), _msg("om_x")):
        ran = True
    assert ran is True


class _FakeSlackClient:
    last: "_FakeSlackClient | None" = None

    def __init__(self, token):
        _FakeSlackClient.last = self
        self.added: list[tuple[str, str, str]] = []
        self.removed: list[tuple[str, str, str]] = []

    async def add_reaction(self, channel, ts, name):
        self.added.append((channel, ts, name))

    async def remove_reaction(self, channel, ts, name):
        self.removed.append((channel, ts, name))


@dataclass
class _SlackCred:
    bot_token: str = "xoxb-1"
    agent_id: str = "agent_a"


@pytest.mark.asyncio
async def test_slack_indicator_success_eyes_to_check(monkeypatch):
    import xyz_agent_context.module.slack_module.slack_trigger as st

    monkeypatch.setattr(st, "SlackSDKClient", _FakeSlackClient)
    t = st.SlackTrigger()
    ts = "1620000000.000100"
    async with t.processing_indicator(_SlackCred(), _msg(ts)):
        pass
    c = _FakeSlackClient.last
    assert c.added == [("C1", ts, "eyes"), ("C1", ts, "white_check_mark")]
    assert c.removed == [("C1", ts, "eyes")]


@pytest.mark.asyncio
async def test_slack_indicator_error_eyes_to_warning(monkeypatch):
    import xyz_agent_context.module.slack_module.slack_trigger as st

    monkeypatch.setattr(st, "SlackSDKClient", _FakeSlackClient)
    t = st.SlackTrigger()
    ts = "1620000000.000100"
    async with t.processing_indicator(_SlackCred(), _msg(ts)) as handle:
        handle.set_error(True)
    c = _FakeSlackClient.last
    assert c.added == [("C1", ts, "eyes"), ("C1", ts, "warning")]


class _FakeDiscordClient:
    last: "_FakeDiscordClient | None" = None

    def __init__(self, token):
        _FakeDiscordClient.last = self
        self.added: list[tuple[str, str, str]] = []
        self.removed: list[tuple[str, str, str]] = []

    async def add_reaction(self, channel_id, message_id, emoji):
        self.added.append((channel_id, message_id, emoji))

    async def remove_own_reaction(self, channel_id, message_id, emoji):
        self.removed.append((channel_id, message_id, emoji))


@dataclass
class _DiscordCred:
    bot_token: str = "bot-1"
    agent_id: str = "agent_a"


@pytest.mark.asyncio
async def test_discord_indicator_success_keyboard_to_check(monkeypatch):
    import xyz_agent_context.module.discord_module.discord_trigger as dt

    monkeypatch.setattr(dt, "DiscordSDKClient", _FakeDiscordClient)
    t = dt.DiscordTrigger()
    async with t.processing_indicator(_DiscordCred(), _msg("123")):
        pass
    c = _FakeDiscordClient.last
    assert c.added == [("C1", "123", "⌨️"), ("C1", "123", "✅")]
    assert c.removed == [("C1", "123", "⌨️")]


@pytest.mark.asyncio
async def test_discord_indicator_error_keyboard_to_warning(monkeypatch):
    import xyz_agent_context.module.discord_module.discord_trigger as dt

    monkeypatch.setattr(dt, "DiscordSDKClient", _FakeDiscordClient)
    t = dt.DiscordTrigger()
    async with t.processing_indicator(_DiscordCred(), _msg("123")) as handle:
        handle.set_error(True)
    c = _FakeDiscordClient.last
    assert c.added == [("C1", "123", "⌨️"), ("C1", "123", "⚠️")]


@dataclass
class _WeChatCred:
    bot_token: str = "wx-1"
    agent_id: str = "agent_a"


@pytest.mark.asyncio
async def test_wechat_inherits_noop_indicator():
    import xyz_agent_context.module.wechat_module.wechat_trigger as wt

    t = wt.WeChatTrigger()
    # iLink v1 has no typing/reaction capability → base default no-op handle.
    async with t.processing_indicator(_WeChatCred(), _msg()) as handle:
        assert isinstance(handle, ProcessingIndicatorHandle)
