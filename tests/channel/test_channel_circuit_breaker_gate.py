"""
@file_name: test_channel_circuit_breaker_gate.py
@author: NarraNexus
@date: 2026-09-10
@description: Every IM-channel / A2A turn entry passes the agent
circuit-breaker gate (#394 second review I-3).

Before this, ``ChannelTriggerBase._build_and_run_agent`` (every channel that
does not override it), ``LarkTrigger``'s wholesale override, NarraMessenger's
streaming path and the A2A server started a real turn for every inbound
message with no breaker check at all: a hard-PAUSED agent with a dead key
re-ran a doomed turn per message. Each entry now calls ``admit_turn`` right
before its runtime call, tells the sender why when refused, hands a won
probe token to the runtime call (which settles it) and releases it on the
way out. The silent memory batch only peeks — it never takes the single
probe slot. The real breaker runs on sqlite; only the runtime is faked.
"""
from __future__ import annotations

from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

import narranexus.platform.agent_framework.loop.circuit_breaker as cb
from narranexus.platform.agent_runtime.run_collector import RunCollection
from narranexus.platform.repository.agent_circuit_breaker_repository import (
    AgentCircuitBreakerRepository,
)
from narranexus.platform.schema import CbStatus, ErrorCategory, PausedReason
from narranexus.platform.schema.parsed_message import ParsedMessage
from narranexus.platform.utils.timezone import utc_now
from tests.channel.test_mock_channel_trigger_integration import (
    _FakeCredential,
    _FakeTrigger,
)

AGENT = "agent_a"


class _Client:
    """Runtime client double: records every call's kwargs and the breaker
    row as the turn saw it. It never settles a probe itself, so after the
    call the entry's exit belt must have handed a claim back."""

    def __init__(self, db=None, raises: BaseException | None = None):
        self.calls: list[dict] = []
        self.rows_at_call: list = []
        self._db = db
        self._raises = raises

    async def _snapshot(self):
        if self._db is not None:
            self.rows_at_call.append(await AgentCircuitBreakerRepository(self._db).get(AGENT))

    async def run_and_collect(self, **kw):
        self.calls.append(kw)
        await self._snapshot()
        if self._raises is not None:
            raise self._raises
        return RunCollection(output_text="hello")

    def run_stream(self, **kw):
        self.calls.append(kw)

        async def _gen():
            await self._snapshot()
            if False:  # pragma: no cover — makes this an async generator
                yield None
        return _gen()


class _Trigger(_FakeTrigger):
    def __init__(self):
        super().__init__([], _FakeCredential(agent_id=AGENT))
        self.sent: list[str] = []

    async def send_channel_reply(self, credential, message, text) -> None:
        self.sent.append(text)


def _msg() -> ParsedMessage:
    return ParsedMessage(
        message_id="m1", chat_id="C1", sender_id="u1",
        sender_name="Alice", content="hi", timestamp_ms=1,
    )


@pytest.fixture
def client(monkeypatch, db_client):
    async def _db():
        return db_client
    monkeypatch.setattr(cb, "get_db_client", _db)
    monkeypatch.setattr("narranexus.platform.utils.db.db_factory.get_db_client", _db)
    holder = {"client": _Client(db_client)}
    monkeypatch.setattr(
        "narranexus.platform.agent_runtime.client.get_agent_runtime_client",
        lambda: holder["client"],
    )
    # The A2A server binds the factory at import time.
    monkeypatch.setattr(
        "narranexus_plugins.chat_module.chat_trigger.get_agent_runtime_client",
        lambda: holder["client"],
    )
    return holder


async def _set(db, status: CbStatus, *, due: bool) -> None:
    await AgentCircuitBreakerRepository(db).upsert_state(AGENT, {
        "cb_status": status.value,
        "paused_reason": PausedReason.AUTH.value if status == CbStatus.PAUSED else None,
        "failure_category": ErrorCategory.AUTH.value,
        "consecutive_failure_count": 3,
        "cooldown_until": utc_now() + (timedelta(seconds=-1) if due else timedelta(minutes=5)),
    })


async def _row(db):
    return await AgentCircuitBreakerRepository(db).get(AGENT)


async def _run_base(trigger, db):
    trigger._db = db
    return await trigger._build_and_run_agent(
        trigger._credential, _msg(), "Alice", attachments=[]
    )


# ── ChannelTriggerBase._build_and_run_agent ─────────────────────────────


@pytest.mark.asyncio
async def test_a_paused_agent_runs_no_turn_and_the_sender_is_told(client, db_client):
    await _set(db_client, CbStatus.PAUSED, due=False)
    trigger = _Trigger()
    out = await _run_base(trigger, db_client)
    assert client["client"].calls == []
    assert trigger.sent == [out]
    assert "paused" in out.lower()


@pytest.mark.asyncio
async def test_a_cooling_agent_asks_to_try_again(client, db_client):
    await _set(db_client, CbStatus.COOLING, due=False)
    trigger = _Trigger()
    out = await _run_base(trigger, db_client)
    assert client["client"].calls == []
    assert "try again shortly" in out.lower()


@pytest.mark.asyncio
async def test_an_open_half_open_window_claims_and_hands_the_token_on(client, db_client):
    await _set(db_client, CbStatus.PAUSED, due=True)
    trigger = _Trigger()
    await _run_base(trigger, db_client)
    (call,) = client["client"].calls
    (seen,) = client["client"].rows_at_call
    assert seen.cb_status == CbStatus.PROBING.value
    assert call["probe_token"] == seen.probe_token and seen.probe_token
    assert trigger.sent == []
    # The double never settled; the exit belt handed the claim back.
    after = await _row(db_client)
    assert after.cb_status == CbStatus.PAUSED.value and after.probe_token is None


@pytest.mark.asyncio
async def test_a_healthy_agent_runs_without_a_token(client, db_client):
    trigger = _Trigger()
    out = await _run_base(trigger, db_client)
    (call,) = client["client"].calls
    assert call["probe_token"] is None
    assert out  # the agent's own output, not a refusal
    assert await _row(db_client) is None


@pytest.mark.asyncio
async def test_a_probe_whose_runtime_call_raises_is_handed_back(client, db_client):
    """The runtime call never reached its own settlement: the exit belt
    returns the claim instead of leaving every entry refused until the
    grant expires."""
    await _set(db_client, CbStatus.PAUSED, due=True)
    client["client"] = _Client(db_client, raises=RuntimeError("runtime construction failed"))
    trigger = _Trigger()
    await _run_base(trigger, db_client)
    row = await _row(db_client)
    assert row.cb_status == CbStatus.PAUSED.value
    assert row.probe_token is None
    assert row.consecutive_failure_count == 3


# ── silent memory batch: peek only ──────────────────────────────────────


@pytest.mark.asyncio
async def test_the_silent_batch_never_claims_the_probe(client, db_client):
    trigger = _Trigger()
    trigger._db = db_client
    await _set(db_client, CbStatus.PAUSED, due=True)
    await trigger._build_and_run_agent_silent_batch(trigger._credential, [_msg()])
    assert client["client"].calls == []
    assert (await _row(db_client)).cb_status == CbStatus.PAUSED.value

    await AgentCircuitBreakerRepository(db_client).upsert_state(AGENT, cb._CLEAN_STATE)
    await trigger._build_and_run_agent_silent_batch(trigger._credential, [_msg()])
    (call,) = client["client"].calls
    assert call["silent"] is True
    assert "probe_token" not in call


# ── LarkTrigger overrides _build_and_run_agent wholesale ────────────────


class _LarkCred:
    agent_id = AGENT
    app_id = "cli_test"
    app_secret_ref = "ref"
    brand = "lark"
    profile_name = "p"


class _LarkBuilder:
    def __init__(self, *a, **k):
        pass

    async def build_prompt(self, _history_config):
        return "prompt"

    async def build_retrieval_anchor(self):
        return None


async def _run_lark(monkeypatch, db):
    from narranexus_plugins.lark_module import lark_trigger as lt_mod
    from narranexus_plugins.lark_module.lark_trigger import LarkTrigger

    monkeypatch.setattr(lt_mod, "LarkContextBuilder", _LarkBuilder)
    trigger = LarkTrigger()
    trigger._db = db
    trigger._cli = SimpleNamespace(send_message=AsyncMock())
    out = await trigger._build_and_run_agent(
        cred=_LarkCred(), event={"chat_type": "p2p"}, chat_id="oc", sender_id="ou",
        sender_name="Alice", text="hi", message_id="lm1",
    )
    return trigger, out


@pytest.mark.asyncio
async def test_lark_refuses_a_paused_agent(client, db_client, monkeypatch):
    await _set(db_client, CbStatus.PAUSED, due=False)
    trigger, out = await _run_lark(monkeypatch, db_client)
    assert client["client"].calls == []
    trigger._cli.send_message.assert_awaited_once()
    assert trigger._cli.send_message.await_args.kwargs["text"] == out


@pytest.mark.asyncio
async def test_lark_claims_an_open_window(client, db_client, monkeypatch):
    await _set(db_client, CbStatus.PAUSED, due=True)
    await _run_lark(monkeypatch, db_client)
    (call,) = client["client"].calls
    (seen,) = client["client"].rows_at_call
    assert call["probe_token"] and call["probe_token"] == seen.probe_token
    assert (await _row(db_client)).probe_token is None


# ── NarraMessenger streaming path ───────────────────────────────────────


async def _run_matrix(monkeypatch, db):
    from narranexus_plugins.narramessenger_module.matrix_trigger import MatrixTrigger

    trigger = MatrixTrigger()
    trigger._db = db
    monkeypatch.setattr(trigger, "create_context_builder", lambda *a, **k: _LarkBuilder())
    monkeypatch.setattr(trigger, "_resolve_agent_owner", AsyncMock(return_value="owner"))
    sends = AsyncMock()
    monkeypatch.setattr(trigger, "_send_matrix_reply", sends)
    cred = SimpleNamespace(agent_id=AGENT)
    msg = SimpleNamespace(
        chat_id="!r", message_id="$e", sender_id="@u:h", content="hi",
        raw={}, timestamp_ms=0, sender_name="U",
    )
    out = await trigger._build_and_run_agent_streaming(cred, msg, "U", attachments=None)
    return sends, out


@pytest.mark.asyncio
async def test_matrix_streaming_refuses_a_paused_agent(client, db_client, monkeypatch):
    await _set(db_client, CbStatus.PAUSED, due=False)
    sends, out = await _run_matrix(monkeypatch, db_client)
    assert client["client"].calls == []
    sends.assert_awaited_once()
    assert sends.await_args.args[2] == out and "paused" in out.lower()


@pytest.mark.asyncio
async def test_matrix_streaming_claims_an_open_window(client, db_client, monkeypatch):
    await _set(db_client, CbStatus.PAUSED, due=True)
    await _run_matrix(monkeypatch, db_client)
    (call,) = client["client"].calls
    (seen,) = client["client"].rows_at_call
    assert call["probe_token"] and call["probe_token"] == seen.probe_token
    assert (await _row(db_client)).probe_token is None


# ── A2A server (tasks/send) ─────────────────────────────────────────────


def _a2a_params() -> dict:
    return {"message": {
        "role": "user",
        "parts": [{"type": "text", "text": "hi"}],
        "metadata": {"agent_id": AGENT, "user_id": "u"},
    }}


async def _a2a_send(db):
    from narranexus_plugins.chat_module.chat_trigger import A2AServer

    server = A2AServer(database_client=db)
    return await server._handle_tasks_send("1", _a2a_params())


@pytest.mark.asyncio
async def test_a2a_send_refuses_a_paused_agent(client, db_client):
    await _set(db_client, CbStatus.PAUSED, due=False)
    task = await _a2a_send(db_client)
    assert client["client"].calls == []
    assert task["status"]["state"] == "failed"
    assert "paused:auth" in str(task["status"]["message"])


@pytest.mark.asyncio
async def test_a2a_send_claims_an_open_window_and_runs_healthy(client, db_client):
    await _set(db_client, CbStatus.PAUSED, due=True)
    await _a2a_send(db_client)
    (call,) = client["client"].calls
    (seen,) = client["client"].rows_at_call
    assert call["probe_token"] and call["probe_token"] == seen.probe_token
    assert (await _row(db_client)).probe_token is None

    await AgentCircuitBreakerRepository(db_client).upsert_state(AGENT, cb._CLEAN_STATE)
    await _a2a_send(db_client)
    assert len(client["client"].calls) == 2
    assert client["client"].calls[-1]["probe_token"] is None
