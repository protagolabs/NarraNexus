"""
@file_name: test_matrix_typing_indicator.py
@date: 2026-11-25
@description: MatrixTrigger.processing_indicator — Matrix ``m.typing``
while the agent works on a dm / group_mention turn.

Contract locked here:
- Entering reports typing=true at once (server timeout
  ``TYPING_TIMEOUT_MS``) and the room's pump refreshes it every
  ``TYPING_REFRESH_SECONDS`` until the last turn exits.
- Exit (normal / exception / cancellation) is synchronous: it drops the
  count and wakes the pump without awaiting any request, even when the
  homeserver hangs. The pump then reports typing=false (soon, bounded
  by ``TYPING_REQUEST_TIMEOUT_SECONDS``) and removes itself; the body's
  exception propagates unchanged.
- One pump per (agent, room) sends every report serially: concurrent
  turns do not multiply the refreshes, a turn entering while the false
  is in flight ends with typing=true, and a turn that enters and leaves
  during that false leaves no stray typing=true behind.
- ``stop()`` cancels the pumps and empties both tables before the
  clients close.
- A failed typing request (raised exception OR nio error response) is
  cosmetic: the body is unaffected and no credential side effect fires.
- Voice-call turns (``raw["rtc_voice"]``) and agents without an active
  client send nothing; no client is ever created for the indicator.
- Every send goes through the subscriber's CURRENT client: a client
  popped mid-turn (unbind / stop) receives nothing more, a replaced one
  (reconnect) hands the refreshes and the final typing=false to the new
  client. A client retired while its request was in flight (or in nio's
  retry back-off) is closed when that request ends; the live client never
  is. Verified against a real nio client and a local aiohttp server too.
- Concurrent turns of one agent in one room share the indicator through a
  reference count: only the last exit clears it, and the count table
  empties again.
- Wiring: the real ``_process_message`` fires the indicator on an
  authorized dm or group_mention turn, never on the group_silent path, an
  authorize denial, or an ingress-breaker rejection.

Timing: assertions wait on conditions (bounded polling) rather than fixed
sleeps; the only sleeps left back "nothing happened" assertions.
"""
from __future__ import annotations

import asyncio
import time
from types import SimpleNamespace
from typing import Callable, Optional
from unittest.mock import AsyncMock

import pytest
from nio import RoomGetStateEventError, RoomTypingError, RoomTypingResponse

from narranexus_plugins.narramessenger_module import matrix_trigger as mt
from narranexus_plugins.narramessenger_module._narramessenger_credential_manager import (
    NarramessengerCredential,
)
from narranexus_plugins.narramessenger_module.matrix_trigger import (
    MatrixTrigger,
    _AuthorizeVerdict,
)
from narranexus.platform.schema.parsed_message import ChatType, ParsedMessage


ROOM = "!room:matrix.netmind.chat"
OTHER_ROOM = "!other:matrix.netmind.chat"
AGENT_MXID = "@agent-abc:matrix.netmind.chat"
USER_MXID = "@alice:matrix.netmind.chat"
THIRD_MXID = "@bob:matrix.netmind.chat"

# Upper bound for "this happens soon" waits. Generous so a loaded CI box
# does not flake; every wait returns as soon as its condition holds.
WAIT_LIMIT_SECONDS = 5.0


async def _until(condition: Callable[[], object]) -> None:
    async def _poll() -> None:
        while not condition():
            await asyncio.sleep(0.005)

    await asyncio.wait_for(_poll(), timeout=WAIT_LIMIT_SECONDS)


async def _settled(t: MatrixTrigger) -> None:
    """Wait (bounded) until every pump has sent its last report and gone."""
    await _until(lambda: not t._typing_pumps)


def _cred(agent_id: str = "agent_x") -> NarramessengerCredential:
    return NarramessengerCredential(
        agent_id=agent_id,
        bearer_token="narra-bearer",
        backend_base_url="https://api.netmind.chat",
        matrix_user_id=AGENT_MXID,
    )


def _msg(
    body: str = "hello",
    *,
    room: str = ROOM,
    raw_extra: Optional[dict] = None,
    chat_type: ChatType = ChatType.PRIVATE,
    mentions: Optional[list[str]] = None,
) -> ParsedMessage:
    content: dict = {}
    if mentions is not None:
        content["m.mentions"] = {"user_ids": list(mentions)}
    raw: dict = {
        "kind": "m.room.message.text",
        "body": body,
        "_nio_event": SimpleNamespace(source={"content": content}),
    }
    raw.update(raw_extra or {})
    return ParsedMessage(
        message_id="$evt1",
        chat_id=room,
        sender_id=USER_MXID,
        sender_name=USER_MXID,
        content=body,
        chat_type=chat_type,
        timestamp_ms=1,
        raw=raw,
    )


class _TypingClient:
    """nio ``AsyncClient`` stand-in recording ``room_typing`` calls.

    ``fail``: None (success response), "raise" (transport exception) or
    "error" (nio ``RoomTypingError`` response, which nio returns instead
    of raising). ``stop_gate``: when set, a typing=false request waits on
    it before answering (models a slow homeserver). ``typing_calls`` is in
    request order; ``landed`` is the order the homeserver applied them.
    """

    def __init__(self, *members: str, fail: Optional[str] = None) -> None:
        self.user_id = AGENT_MXID
        self.members = members or (AGENT_MXID, USER_MXID)
        self.fail = fail
        self.typing_calls: list[tuple[str, bool, int]] = []
        self.stop_gate: Optional[asyncio.Event] = None
        self.landed: list[bool] = []
        self.closed = 0
        self.close_error: Optional[Exception] = None
        # Runs once inside the next room_typing call, while it is in flight.
        self.during_typing: Optional[Callable[[], object]] = None
        # When set, typing=true requests wait on it (a request that hangs).
        self.typing_gate: Optional[asyncio.Event] = None

    async def close(self):
        self.closed += 1
        if self.close_error is not None:
            raise self.close_error

    async def room_typing(self, room_id, typing_state=True, timeout=30000):
        self.typing_calls.append((room_id, typing_state, timeout))
        hook, self.during_typing = self.during_typing, None
        if hook is not None:
            hook()
        if typing_state and self.typing_gate is not None:
            await self.typing_gate.wait()
        if not typing_state and self.stop_gate is not None:
            await self.stop_gate.wait()
        self.landed.append(typing_state)
        if self.fail == "raise":
            raise RuntimeError("homeserver unreachable")
        if self.fail == "error":
            return RoomTypingError("forbidden", "M_FORBIDDEN")
        return RoomTypingResponse(room_id)

    async def joined_members(self, room_id):
        return SimpleNamespace(
            members=[
                SimpleNamespace(user_id=m, display_name=m.split(":")[0][1:])
                for m in self.members
            ]
        )

    async def room_get_state_event(self, room_id, event_type, state_key=""):
        return RoomGetStateEventError(
            "Event not found.", "M_NOT_FOUND", room_id=room_id
        )

    def states(self, room: str = ROOM) -> list[bool]:
        return [state for r, state, _ in self.typing_calls if r == room]


def _trigger_with(client: _TypingClient) -> MatrixTrigger:
    t = MatrixTrigger()
    t._clients[t._subscriber_key(_cred())] = client  # type: ignore[assignment]
    return t


# ────────────────────────────────────────────────────────────────────
# processing_indicator — the pump
# ────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_entering_reports_typing_true_at_once():
    client = _TypingClient()
    t = _trigger_with(client)
    t.TYPING_REFRESH_SECONDS = 30.0

    async with t.processing_indicator(_cred(), _msg()):
        await _until(lambda: client.typing_calls)
        assert client.typing_calls == [(ROOM, True, t.TYPING_TIMEOUT_MS)]
    assert t.TYPING_TIMEOUT_MS == 30000


@pytest.mark.asyncio
async def test_instant_body_still_reports_true_then_false():
    """A body with no await at all still shows the indicator once and
    clears it — the first report does not depend on the pump winning a
    scheduling race against the exit."""
    client = _TypingClient()
    t = _trigger_with(client)

    async with t.processing_indicator(_cred(), _msg()):
        pass
    await _settled(t)
    assert client.states() == [True, False]


@pytest.mark.asyncio
async def test_long_run_refreshes_typing():
    client = _TypingClient()
    t = _trigger_with(client)
    t.TYPING_REFRESH_SECONDS = 0.02

    async with t.processing_indicator(_cred(), _msg()):
        await _until(lambda: client.states().count(True) >= 3)
    await _settled(t)
    assert client.states()[-1] is False


@pytest.mark.asyncio
async def test_exit_reports_false_and_does_not_wait_out_the_refresh():
    client = _TypingClient()
    t = _trigger_with(client)
    # Without a proper wake-up the pump would sleep out this interval
    # before clearing the indicator.
    t.TYPING_REFRESH_SECONDS = 30.0

    async with t.processing_indicator(_cred(), _msg()):
        await _until(lambda: client.typing_calls)
    await _until(lambda: False in client.states())  # well inside 30s
    await _settled(t)
    assert client.typing_calls[-1][:2] == (ROOM, False)
    assert client.states().count(False) == 1


@pytest.mark.asyncio
async def test_slow_stop_request_does_not_hold_the_exit():
    client = _TypingClient()
    client.stop_gate = asyncio.Event()  # never set: typing=false hangs
    t = _trigger_with(client)
    t.TYPING_REQUEST_TIMEOUT_SECONDS = 0.1

    async with t.processing_indicator(_cred(), _msg()):
        await _until(lambda: client.typing_calls)
    # The hung false is abandoned after TYPING_REQUEST_TIMEOUT_SECONDS and
    # the pump still finishes; without the bound it would hang forever.
    await _settled(t)
    assert client.states()[-1] is False


@pytest.mark.asyncio
@pytest.mark.parametrize("hang", ["true", "false"])
async def test_exit_never_waits_for_the_homeserver(hang):
    """The exit returns without awaiting any request, even with every
    bound far away and the homeserver not answering at all."""
    client = _TypingClient()
    gate = asyncio.Event()
    if hang == "true":
        client.typing_gate = gate  # the entry's typing=true never answers
    else:
        client.stop_gate = gate  # the clearing typing=false never answers
    t = _trigger_with(client)
    t.TYPING_REFRESH_SECONDS = 30.0
    t.TYPING_REQUEST_TIMEOUT_SECONDS = 30.0

    async with t.processing_indicator(_cred(), _msg()):
        await _until(lambda: client.typing_calls)
        if hang == "false":
            await _until(lambda: client.landed == [True])
        start = time.monotonic()
    elapsed = time.monotonic() - start
    assert elapsed < 0.5, f"exit took {elapsed:.2f}s"
    assert t._typing_refs == {}
    if hang == "false":
        await _until(lambda: False in client.states())  # in flight, hung
    assert t._typing_pumps  # still working, off the worker
    gate.set()
    await _settled(t)
    assert client.landed[-1] is False


@pytest.mark.asyncio
async def test_hook_failure_never_reaches_the_body(monkeypatch):
    client = _TypingClient()
    t = _trigger_with(client)

    def _boom(*_a, **_kw):
        raise RuntimeError("pump start failed")

    monkeypatch.setattr(t, "_wake_typing_pump", _boom)
    ran = False
    async with t.processing_indicator(_cred(), _msg()):
        ran = True
    assert ran
    assert t._typing_refs == {}
    assert t._typing_pumps == {}


@pytest.mark.asyncio
async def test_body_exception_propagates_and_typing_is_cleared():
    client = _TypingClient()
    t = _trigger_with(client)

    class _AgentBoom(Exception):
        pass

    with pytest.raises(_AgentBoom, match="agent failed"):
        async with t.processing_indicator(_cred(), _msg()):
            await _until(lambda: client.typing_calls)
            raise _AgentBoom("agent failed")
    assert t._typing_refs == {}  # dropped synchronously on exit
    await _settled(t)
    assert client.states() == [True, False]
    assert t._typing_refs == {}


@pytest.mark.asyncio
async def test_cancelled_body_clears_typing():
    client = _TypingClient()
    t = _trigger_with(client)

    async def _turn():
        async with t.processing_indicator(_cred(), _msg()):
            await asyncio.sleep(60)

    task = asyncio.create_task(_turn())
    await _until(lambda: client.typing_calls)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert t._typing_refs == {}  # dropped synchronously on exit
    await _settled(t)
    assert client.states() == [True, False]


@pytest.mark.asyncio
@pytest.mark.parametrize("fail", ["raise", "error"])
async def test_failed_typing_request_never_reaches_the_body(fail):
    client = _TypingClient(fail=fail)
    t = _trigger_with(client)
    t.TYPING_REFRESH_SECONDS = 0.02
    disable = AsyncMock()
    t.disable_credential = disable  # type: ignore[method-assign]

    ran = False
    async with t.processing_indicator(_cred(), _msg()):
        await _until(lambda: client.states().count(True) >= 2)  # pump kept going
        ran = True
    assert ran
    await _settled(t)
    assert client.states()[-1] is False
    disable.assert_not_awaited()
    assert t._typing_refs == {}


@pytest.mark.asyncio
async def test_voice_turn_sends_no_typing():
    client = _TypingClient()
    t = _trigger_with(client)
    voice = _msg(raw_extra={"rtc_voice": {"rtc_session_id": "s1", "turn_id": "t1"}})

    async with t.processing_indicator(_cred(), voice):
        await asyncio.sleep(0.05)  # room for a pump that should not exist
    assert client.typing_calls == []
    assert t._typing_refs == {}


@pytest.mark.asyncio
async def test_no_active_client_is_a_no_op(monkeypatch):
    other = _TypingClient()
    t = MatrixTrigger()
    # Only some other agent has a live client.
    t._clients[t._subscriber_key(_cred("agent_other"))] = other  # type: ignore[assignment]

    def _no_new_clients(*_a, **_kw):
        raise AssertionError("the indicator must not create a client")

    monkeypatch.setattr(mt, "AsyncClient", _no_new_clients)

    ran = False
    async with t.processing_indicator(_cred(), _msg()):
        await asyncio.sleep(0.05)  # room for a pump that should not exist
        ran = True
    assert ran
    assert other.typing_calls == []
    assert list(t._clients) == [t._subscriber_key(_cred("agent_other"))]
    assert t._typing_refs == {}


# ────────────────────────────────────────────────────────────────────
# The client is looked up at send time
# ────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_client_popped_mid_turn_receives_nothing_more():
    """Unbind / stop pops the client and closes it. A request on a closed
    nio client silently opens a new, never-closed aiohttp session, so the
    popped client must not see a refresh or the exit's typing=false."""
    old = _TypingClient()
    t = _trigger_with(old)
    t.TYPING_REFRESH_SECONDS = 0.02
    key = t._subscriber_key(_cred())

    async with t.processing_indicator(_cred(), _msg()):
        await _until(lambda: old.typing_calls)
        t._clients.pop(key)
        calls_at_pop = len(old.typing_calls)
        await asyncio.sleep(0.15)  # several refresh intervals
    await _settled(t)  # the pump's clearing pass is over too
    assert len(old.typing_calls) == calls_at_pop
    assert False not in old.states()
    assert t._typing_refs == {}


@pytest.mark.asyncio
async def test_reconnected_client_takes_over_refresh_and_clear():
    old = _TypingClient()
    t = _trigger_with(old)
    t.TYPING_REFRESH_SECONDS = 0.02
    key = t._subscriber_key(_cred())
    new = _TypingClient()

    async with t.processing_indicator(_cred(), _msg()):
        await _until(lambda: old.typing_calls)
        t._clients[key] = new  # type: ignore[assignment]
        calls_at_swap = len(old.typing_calls)
        await _until(lambda: new.states().count(True) >= 2)
    await _settled(t)
    assert len(old.typing_calls) == calls_at_swap
    assert False not in old.states()
    assert new.states()[-1] is False
    assert new.closed == 0
    assert t._typing_refs == {}


@pytest.mark.asyncio
async def test_client_retired_during_its_request_is_closed_after_it():
    """nio retries inside room_typing and re-opens a session on a closed
    client; the subscribe loop's close() can land mid-request. The request's
    owner closes the retired client again once the request ends."""
    old = _TypingClient()
    t = _trigger_with(old)
    t.TYPING_REFRESH_SECONDS = 0.02
    key = t._subscriber_key(_cred())
    old.during_typing = lambda: t._clients.pop(key)  # subscribe-loop teardown

    async with t.processing_indicator(_cred(), _msg()):
        await _until(lambda: old.closed)
        await asyncio.sleep(0.1)  # several refresh intervals
    await _settled(t)
    assert old.closed == 1
    assert old.states() == [True]
    assert t._typing_refs == {}


@pytest.mark.asyncio
async def test_retired_client_is_closed_when_its_request_is_cancelled():
    old = _TypingClient()
    old.typing_gate = asyncio.Event()  # never set: the request hangs
    t = _trigger_with(old)
    t.TYPING_REQUEST_TIMEOUT_SECONDS = 0.1  # the bound cancels the stuck request
    key = t._subscriber_key(_cred())
    old.during_typing = lambda: t._clients.pop(key)

    async with t.processing_indicator(_cred(), _msg()):
        await _until(lambda: old.typing_calls)
    await _settled(t)
    assert old.closed == 1
    assert old.states() == [True]
    assert t._typing_refs == {}


@pytest.mark.asyncio
async def test_live_client_is_never_closed():
    client = _TypingClient()
    t = _trigger_with(client)
    t.TYPING_REFRESH_SECONDS = 0.02

    async with t.processing_indicator(_cred(), _msg()):
        await _until(lambda: client.states().count(True) >= 3)
    await _settled(t)
    assert client.states()[-1] is False
    assert client.closed == 0


@pytest.mark.asyncio
async def test_failing_close_of_a_retired_client_is_swallowed():
    old = _TypingClient()
    old.close_error = RuntimeError("close blew up")
    t = _trigger_with(old)
    key = t._subscriber_key(_cred())
    old.during_typing = lambda: t._clients.pop(key)
    disable = AsyncMock()
    t.disable_credential = disable  # type: ignore[method-assign]

    ran = False
    async with t.processing_indicator(_cred(), _msg()):
        await _until(lambda: old.closed)
        ran = True
    assert ran
    await _settled(t)
    disable.assert_not_awaited()
    assert t._typing_refs == {}


@pytest.mark.asyncio
async def test_real_nio_client_retired_during_429_backoff_leaves_no_session():
    """Real nio + local homeserver stand-in: the first typing request gets
    a 429, the subscribe loop pops and closes the client during nio's
    back-off, nio's retry re-opens a session. That session must be closed
    once the request ends."""
    from aiohttp import web
    from aiohttp.test_utils import TestServer
    from nio import AsyncClient

    hits: list[str] = []

    async def _handler(request):
        hits.append(request.path)
        if len(hits) == 1:
            return web.json_response(
                {"errcode": "M_LIMIT_EXCEEDED", "error": "slow down",
                 "retry_after_ms": 200},
                status=429,
            )
        return web.json_response({})

    app = web.Application()
    app.router.add_route("*", "/{tail:.*}", _handler)
    server = TestServer(app)
    await server.start_server()
    client = AsyncClient(str(server.make_url("")).rstrip("/"), AGENT_MXID)
    client.access_token = "token"
    client.user_id = AGENT_MXID
    t = MatrixTrigger()
    t.TYPING_REFRESH_SECONDS = 30.0
    key = t._subscriber_key(_cred())
    t._clients[key] = client
    try:
        async with t.processing_indicator(_cred(), _msg()):
            await _until(lambda: hits)
            # Subscribe-loop teardown while nio sleeps out the 429.
            t._clients.pop(key)
            await client.close()
            await _until(lambda: len(hits) >= 2)
        await _settled(t)
        assert "/typing/" in hits[0]
        assert client.client_session is None
    finally:
        await client.close()
        await server.close()


# ────────────────────────────────────────────────────────────────────
# Concurrent turns in one room — reference count
# ────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_concurrent_turns_only_last_exit_clears_typing():
    client = _TypingClient()
    t = _trigger_with(client)
    t.TYPING_REFRESH_SECONDS = 30.0
    b_release = asyncio.Event()
    b_entered = asyncio.Event()

    async def _turn_a():
        async with t.processing_indicator(_cred(), _msg()):
            await b_entered.wait()

    async def _turn_b():
        async with t.processing_indicator(_cred(), _msg()):
            b_entered.set()
            await b_release.wait()

    task_b = asyncio.create_task(_turn_b())
    await _turn_a()
    # A has exited while B still runs: B's indicator must stay up. Room
    # for the woken pump to (wrongly) clear it before the check.
    await asyncio.sleep(0.05)
    assert False not in client.states()
    assert t._typing_refs == {(t._subscriber_key(_cred()), ROOM): 1}
    assert len(t._typing_pumps) == 1

    b_release.set()
    await task_b
    await _settled(t)
    assert client.states().count(False) == 1
    assert client.states()[-1] is False
    assert t._typing_refs == {}


@pytest.mark.asyncio
async def test_turns_in_different_rooms_are_independent():
    client = _TypingClient()
    t = _trigger_with(client)
    t.TYPING_REFRESH_SECONDS = 30.0
    release = asyncio.Event()
    entered = asyncio.Event()

    async def _long_turn():
        async with t.processing_indicator(_cred(), _msg(room=OTHER_ROOM)):
            entered.set()
            await release.wait()

    task = asyncio.create_task(_long_turn())
    await entered.wait()
    async with t.processing_indicator(_cred(), _msg(room=ROOM)):
        pass
    key = t._subscriber_key(_cred())
    await _until(lambda: (key, ROOM) not in t._typing_pumps)
    assert client.states(ROOM) == [True, False]
    assert False not in client.states(OTHER_ROOM)
    assert list(t._typing_pumps) == [(key, OTHER_ROOM)]

    release.set()
    await task
    await _settled(t)
    assert client.states(OTHER_ROOM)[-1] is False
    assert t._typing_refs == {}


@pytest.mark.asyncio
async def test_turn_starting_while_the_last_one_clears_keeps_typing():
    """Turn A is the last one out and the pump's typing=false is in flight
    when turn B starts. The same pump sends B's typing=true once the false
    has landed, so the room ends up showing B's indicator."""
    client = _TypingClient()
    client.stop_gate = asyncio.Event()
    t = _trigger_with(client)
    t.TYPING_REFRESH_SECONDS = 30.0
    t.TYPING_REQUEST_TIMEOUT_SECONDS = 30.0
    b_release = asyncio.Event()

    async def _turn_a():
        async with t.processing_indicator(_cred(), _msg()):
            pass

    async def _turn_b():
        async with t.processing_indicator(_cred(), _msg()):
            await b_release.wait()

    await _turn_a()
    await _until(lambda: False in client.states())  # the false is in flight
    task_b = asyncio.create_task(_turn_b())
    await _until(lambda: t._typing_refs)  # B has entered
    assert len(t._typing_pumps) == 1  # B woke the same pump
    client.stop_gate.set()  # the false lands
    await _until(lambda: client.landed[-1:] == [True] and len(client.landed) == 3)
    # What the homeserver applied last decides what the room shows.
    assert client.landed == [True, False, True]

    b_release.set()
    await task_b
    await _settled(t)
    assert client.landed[-1] is False
    assert t._typing_refs == {}


@pytest.mark.asyncio
async def test_turn_entering_and_leaving_during_the_clear_leaves_no_stray_true():
    """Reverse race: B enters and leaves while A's clearing typing=false is
    in flight. Once the false lands the count is zero again, so the pump
    stops there — no typing=true lands after the last false."""
    client = _TypingClient()
    client.stop_gate = asyncio.Event()
    t = _trigger_with(client)
    t.TYPING_REFRESH_SECONDS = 30.0
    t.TYPING_REQUEST_TIMEOUT_SECONDS = 30.0

    async with t.processing_indicator(_cred(), _msg()):  # turn A
        pass
    await _until(lambda: False in client.states())  # the false is in flight
    async with t.processing_indicator(_cred(), _msg()):  # turn B, in and out
        pass
    client.stop_gate.set()
    await _settled(t)
    await asyncio.sleep(0.05)  # room for a stray report that must not come
    assert client.landed == [True, False]
    assert t._typing_refs == {}


@pytest.mark.asyncio
async def test_concurrent_turns_share_one_pump_and_its_refreshes():
    """N turns in one room are one pump: the refresh rate does not grow with
    N, and entering / leaving turns do not trigger extra reports."""
    client = _TypingClient()
    t = _trigger_with(client)
    t.TYPING_REFRESH_SECONDS = 0.05
    release = asyncio.Event()
    entered = 0

    async def _turn():
        nonlocal entered
        async with t.processing_indicator(_cred(), _msg()):
            entered += 1
            await release.wait()

    tasks = [asyncio.create_task(_turn()) for _ in range(3)]
    await _until(lambda: entered == 3)
    start = time.monotonic()
    await asyncio.sleep(0.5)
    elapsed = time.monotonic() - start
    assert len(t._typing_pumps) == 1
    trues = client.states().count(True)
    # One pump: about 1 + elapsed / refresh reports. Three pumps (one per
    # turn) would send about three times that.
    assert trues <= 2 + elapsed / t.TYPING_REFRESH_SECONDS, trues

    release.set()
    await asyncio.gather(*tasks)
    await _settled(t)
    assert client.states().count(False) == 1
    assert client.states()[-1] is False


@pytest.mark.asyncio
async def test_crashed_pump_is_replaced_and_its_callback_spares_the_new_one(
    monkeypatch,
):
    """A pump that crashes is logged and removed by its done callback. A turn
    entering after the crash but BEFORE that callback has run (it still sees
    the dead pump registered) starts a new pump instead of waking the dead
    one, and the old callback, running afterwards, leaves the new pump in
    place."""
    from loguru import logger

    client = _TypingClient()
    t = _trigger_with(client)
    t.TYPING_REFRESH_SECONDS = 30.0
    ref_key = (t._subscriber_key(_cred()), ROOM)
    loop = asyncio.get_running_loop()
    seen: dict = {}
    warnings: list[str] = []
    sink = logger.add(lambda m: warnings.append(str(m)), level="WARNING")
    real_send = t._send_typing_bounded
    crashed = False

    def _enter_between_crash_and_callback() -> None:
        # Queued before the crashing task finishes, so it runs after the
        # task is done but before the task's done callbacks.
        seen["dead"] = t._typing_pumps[ref_key].task
        assert seen["dead"].done()
        t._typing_refs[ref_key] = t._typing_refs.get(ref_key, 0) + 1
        t._wake_typing_pump(_cred(), ref_key)
        seen["new"] = t._typing_pumps[ref_key].task

    async def _send(credential, room_id, typing):
        nonlocal crashed
        if not crashed:
            crashed = True
            loop.call_soon(_enter_between_crash_and_callback)
            raise RuntimeError("pump bug")
        await real_send(credential, room_id, typing)

    monkeypatch.setattr(t, "_send_typing_bounded", _send)
    try:
        async with t.processing_indicator(_cred(), _msg()):
            await _until(lambda: "new" in seen)
            await asyncio.sleep(0.02)  # the dead pump's callback has run
            assert seen["new"] is not seen["dead"]
            assert t._typing_pumps[ref_key].task is seen["new"]
            await _until(lambda: client.typing_calls)  # the new pump works
        # Drop the reference the simulated entry added, then let it clear.
        t._typing_refs.pop(ref_key, None)
        t._typing_pumps[ref_key].wake.set()
        await _settled(t)
        assert client.states() == [True, False]
        assert any("typing pump crashed" in w for w in warnings)
    finally:
        logger.remove(sink)


@pytest.mark.asyncio
async def test_stop_cancels_pumps_and_empties_the_tables():
    client = _TypingClient()
    client.typing_gate = asyncio.Event()  # the pump is stuck mid-request
    t = _trigger_with(client)
    t.TYPING_REFRESH_SECONDS = 30.0
    t.TYPING_REQUEST_TIMEOUT_SECONDS = 30.0
    release = asyncio.Event()
    entered = asyncio.Event()

    async def _turn():
        async with t.processing_indicator(_cred(), _msg()):
            entered.set()
            await release.wait()

    turn = asyncio.create_task(_turn())
    await entered.wait()
    await _until(lambda: client.typing_calls)
    pump = next(iter(t._typing_pumps.values())).task

    await asyncio.wait_for(t.stop(), timeout=WAIT_LIMIT_SECONDS)
    assert pump.cancelled()
    assert t._typing_pumps == {}
    assert t._typing_refs == {}
    # Unregistered before the pumps are awaited: the cancelled request
    # sees a retired client and closes it, then stop() closes it again.
    assert client.closed == 2

    # The turn still running exits cleanly and starts nothing new.
    release.set()
    await turn
    assert t._typing_refs == {}
    assert t._typing_pumps == {}
    assert client.states() == [True]


# ────────────────────────────────────────────────────────────────────
# Wiring — the real _process_message
# ────────────────────────────────────────────────────────────────────


class _Pipeline:
    """Real trigger driven through ``_process_message``; only the agent run,
    the authorize-event HTTP call, the ingress breaker and the silent
    buffer are stubbed. ``seen_during_run`` is the typing traffic observed
    from inside the agent run."""

    def __init__(
        self, client: _TypingClient, *, allow: bool = True, admitted: bool = True
    ) -> None:
        self.client = client
        self.trigger = t = MatrixTrigger()
        t.TYPING_REFRESH_SECONDS = 30.0
        cred = _cred()
        key = t._subscriber_key(cred)
        t._clients[key] = client  # type: ignore[assignment]
        t._subscriber_creds[key] = cred
        self.authorize = AsyncMock(return_value=_AuthorizeVerdict(allow=allow))
        self.enqueue_silent = AsyncMock()
        self.ingress = AsyncMock(return_value=admitted)
        setattr(t, "_authorize_event", self.authorize)
        setattr(t, "_send_matrix_notice", AsyncMock())
        setattr(t, "_enqueue_silent", self.enqueue_silent)
        setattr(t, "_ingress_admitted", self.ingress)
        setattr(t, "_inbox_recorder", SimpleNamespace(record_turn=AsyncMock()))
        self.seen_during_run: Optional[list[bool]] = None

        async def _run(credential, message, sender_name, *, attachments=None):
            await _until(lambda: client.typing_calls)
            self.seen_during_run = client.states()
            return "answer"

        setattr(t, "_build_and_run_agent", _run)

    async def process(self, message: ParsedMessage) -> None:
        await self.trigger._process_message(_cred(), message)


@pytest.mark.asyncio
async def test_authorized_dm_turn_reports_typing_through_the_pipeline():
    p = _Pipeline(_TypingClient(AGENT_MXID, USER_MXID))
    await p.process(_msg())
    assert p.authorize.await_args.kwargs["mentioned"] is False
    assert p.seen_during_run == [True]
    await _settled(p.trigger)
    assert p.client.states() == [True, False]
    assert p.trigger._typing_refs == {}


@pytest.mark.asyncio
async def test_group_mention_turn_reports_typing_through_the_pipeline():
    p = _Pipeline(_TypingClient(AGENT_MXID, USER_MXID, THIRD_MXID))
    await p.process(_msg(chat_type=ChatType.GROUP, mentions=[AGENT_MXID]))
    p.enqueue_silent.assert_not_awaited()
    assert p.authorize.await_args.kwargs["mentioned"] is True
    assert p.seen_during_run == [True]
    await _settled(p.trigger)
    assert p.client.states() == [True, False]
    assert p.trigger._typing_refs == {}


@pytest.mark.asyncio
async def test_group_silent_turn_sends_no_typing():
    p = _Pipeline(_TypingClient(AGENT_MXID, USER_MXID, THIRD_MXID))
    await p.process(_msg(chat_type=ChatType.GROUP))
    p.enqueue_silent.assert_awaited_once()
    assert p.seen_during_run is None
    assert p.client.typing_calls == []


@pytest.mark.asyncio
async def test_denied_turn_sends_no_typing():
    p = _Pipeline(_TypingClient(AGENT_MXID, USER_MXID), allow=False)
    await p.process(_msg())
    p.authorize.assert_awaited_once()
    assert p.seen_during_run is None
    assert p.client.typing_calls == []


@pytest.mark.asyncio
async def test_ingress_breaker_rejection_sends_no_typing():
    p = _Pipeline(_TypingClient(AGENT_MXID, USER_MXID), admitted=False)
    await p.process(_msg())
    p.authorize.assert_awaited_once()
    p.ingress.assert_awaited_once()
    assert p.seen_during_run is None
    assert p.client.typing_calls == []
