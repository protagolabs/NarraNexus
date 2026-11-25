"""
@file_name: test_matrix_live_client.py
@date: 2026-11-25
@description: MatrixTrigger._with_live_client — every homeserver request
outside the sync loop runs on the subscriber's CURRENT client, and a
client retired while its request was in flight is closed afterwards.

Contract locked here:
- No registered client → the sentinel comes back and the request never
  runs.
- The request's result and exceptions (cancellation included) pass
  through unchanged.
- A client popped or replaced during its request is closed once the
  request ends — normally, by error, or by cancellation. The live client
  is never closed.
- The reply sender looks the client up again on every attempt: a retry
  after a reconnect goes to the new client. No client before the first
  attempt OR before a retry ends in the ``no_active_client`` audit, never
  silently.
- The notice sender and the room-shape lookups (joined_members, creation
  state) go through the same path; no client there is a failed lookup.
- ``is_voice_turn`` / ``rtc_voice_stamp`` read the voice stamp.
"""
from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Callable, Optional

import pytest
from nio import RoomGetStateEventError, RoomSendError, RoomSendResponse

from narranexus_plugins.narramessenger_module._narramessenger_credential_manager import (
    NarramessengerCredential,
)
from narranexus_plugins.narramessenger_module.matrix_trigger import (
    _NO_LIVE_CLIENT,
    MatrixTrigger,
)
from narranexus_plugins.narramessenger_module.voice._rtc_voice import (
    is_voice_turn,
    rtc_voice_stamp,
)
from narranexus.platform.schema.parsed_message import ChatType, ParsedMessage

ROOM = "!room:h"
AGENT_MXID = "@agent-abc:h"
USER_MXID = "@alice:h"


def _cred() -> NarramessengerCredential:
    return NarramessengerCredential(
        agent_id="agent_x",
        bearer_token="tok",
        matrix_user_id=AGENT_MXID,
    )


class _AuditRecorder:
    def __init__(self) -> None:
        self.calls: list = []

    async def append(self, event_type, **kwargs):
        self.calls.append((event_type, kwargs))


class _Client:
    """nio ``AsyncClient`` stand-in. ``sends`` holds the responses (or
    exceptions) ``room_send`` returns in order; ``during`` runs inside the
    next request while it is in flight."""

    def __init__(self, *sends) -> None:
        self.sends = list(sends)
        self.sent: list[dict] = []
        self.closed = 0
        self.joined_calls = 0
        self.state_calls = 0
        self.during: Optional[Callable[[], object]] = None

    def _in_flight(self) -> None:
        hook, self.during = self.during, None
        if hook is not None:
            hook()

    async def close(self):
        self.closed += 1

    async def room_send(self, **kwargs):
        self.sent.append(kwargs)
        self._in_flight()
        resp = self.sends.pop(0)
        if isinstance(resp, BaseException):
            raise resp
        return resp

    async def joined_members(self, room_id):
        self.joined_calls += 1
        self._in_flight()
        return SimpleNamespace(
            members=[
                SimpleNamespace(user_id=m, display_name=m) for m in (AGENT_MXID, USER_MXID)
            ]
        )

    async def room_get_state_event(self, room_id, event_type, state_key=""):
        self.state_calls += 1
        self._in_flight()
        return RoomGetStateEventError("Event not found.", "M_NOT_FOUND", room_id=room_id)


def _ok() -> RoomSendResponse:
    return RoomSendResponse(event_id="$sent", room_id=ROOM)


def _err(code: str) -> RoomSendError:
    return RoomSendError(message=code, status_code=code, room_id=ROOM)


@pytest.fixture
def t():
    trigger = MatrixTrigger()
    trigger.SEND_INITIAL_BACKOFF_MS = 1
    trigger.SEND_MAX_ATTEMPTS = 3
    trigger._audit_repo = _AuditRecorder()
    return trigger


def _key(t: MatrixTrigger) -> str:
    return t._subscriber_key(_cred())


# ────────────────────────────────────────────────────────────────────
# The helper
# ────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_no_client_returns_the_sentinel_without_running_the_request(t):
    ran = False

    async def _request(client):
        nonlocal ran
        ran = True

    assert await t._with_live_client(_cred(), _request) is _NO_LIVE_CLIENT
    assert ran is False


@pytest.mark.asyncio
async def test_result_passes_through_and_the_live_client_stays_open(t):
    client = _Client()
    t._clients[_key(t)] = client

    async def _request(c):
        assert c is client
        return "result"

    assert await t._with_live_client(_cred(), _request) == "result"
    assert client.closed == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("retire", ["pop", "replace"])
async def test_client_retired_during_the_request_is_closed_after_it(t, retire):
    old = _Client()
    new = _Client()
    t._clients[_key(t)] = old

    async def _request(c):
        if retire == "pop":
            t._clients.pop(_key(t))
        else:
            t._clients[_key(t)] = new
        await asyncio.sleep(0)
        assert c.closed == 0  # not before the request ends
        return "done"

    assert await t._with_live_client(_cred(), _request) == "done"
    assert old.closed == 1
    assert new.closed == 0


@pytest.mark.asyncio
async def test_retired_client_is_closed_when_the_request_raises(t):
    old = _Client()
    t._clients[_key(t)] = old

    async def _request(c):
        t._clients.pop(_key(t))
        raise RuntimeError("transport down")

    with pytest.raises(RuntimeError, match="transport down"):
        await t._with_live_client(_cred(), _request)
    assert old.closed == 1


@pytest.mark.asyncio
async def test_retired_client_is_closed_when_the_request_is_cancelled(t):
    old = _Client()
    t._clients[_key(t)] = old
    started = asyncio.Event()

    async def _request(c):
        t._clients.pop(_key(t))
        started.set()
        await asyncio.sleep(60)

    task = asyncio.create_task(t._with_live_client(_cred(), _request))
    await started.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert old.closed == 1


@pytest.mark.asyncio
async def test_failing_close_of_a_retired_client_does_not_mask_the_result(t):
    old = _Client()

    async def _close():
        raise RuntimeError("close blew up")

    old.close = _close  # type: ignore[method-assign]
    t._clients[_key(t)] = old

    async def _request(c):
        t._clients.pop(_key(t))
        return "done"

    assert await t._with_live_client(_cred(), _request) == "done"


# ────────────────────────────────────────────────────────────────────
# Reply sender
# ────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_reply_retry_after_a_reconnect_uses_the_new_client(t):
    old = _Client(_err("M_UNKNOWN"))
    new = _Client(_ok())
    t._clients[_key(t)] = old
    old.during = lambda: t._clients.__setitem__(_key(t), new)  # reconnect

    assert await t._send_matrix_reply(_cred(), ROOM, "hi") is True
    assert len(old.sent) == 1
    assert len(new.sent) == 1
    assert new.sent[0]["content"] == old.sent[0]["content"]
    assert old.closed == 1  # retired during its attempt
    assert new.closed == 0
    assert t._audit_repo.calls == []


@pytest.mark.asyncio
async def test_reply_without_a_client_before_a_retry_is_audited(t):
    old = _Client(_err("M_UNKNOWN"))
    t._clients[_key(t)] = old
    old.during = lambda: t._clients.pop(_key(t))  # unbind mid-send

    assert await t._send_matrix_reply(_cred(), ROOM, "hello there") is False
    assert len(old.sent) == 1
    assert old.closed == 1
    [(event_type, kwargs)] = t._audit_repo.calls
    assert event_type == "transport_send_failed"
    details = kwargs["details"]
    assert details["error_code"] == "no_active_client"
    assert details["attempts"] == 1
    assert details["previous_error_code"] == "M_UNKNOWN"
    assert details["body_preview"] == "hello there"


@pytest.mark.asyncio
async def test_reply_without_a_client_at_all_is_audited_with_zero_attempts(t):
    assert await t._send_matrix_reply(_cred(), ROOM, "hello") is False
    [(event_type, kwargs)] = t._audit_repo.calls
    assert event_type == "transport_send_failed"
    assert kwargs["details"] == {
        "error_code": "no_active_client",
        "attempts": 0,
        "body_preview": "hello",
    }


@pytest.mark.asyncio
async def test_reply_retries_on_the_live_client_never_close_it(t):
    client = _Client(RuntimeError("boom"), _err("M_UNKNOWN"), _ok())
    t._clients[_key(t)] = client

    assert await t._send_matrix_reply(_cred(), ROOM, "hi") is True
    assert len(client.sent) == 3
    assert client.closed == 0


# ────────────────────────────────────────────────────────────────────
# Notice sender and room-shape lookups
# ────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_notice_goes_to_the_live_client_and_closes_a_retired_one(t):
    old = _Client(_ok())
    t._clients[_key(t)] = old
    old.during = lambda: t._clients.pop(_key(t))

    await t._send_matrix_notice(_cred(), ROOM, "not allowed")
    assert old.sent[0]["content"]["msgtype"] == "m.notice"
    assert old.closed == 1


@pytest.mark.asyncio
async def test_notice_without_a_client_sends_nothing(t):
    await t._send_matrix_notice(_cred(), ROOM, "not allowed")  # no raise


@pytest.mark.asyncio
async def test_room_shape_lookups_use_the_live_client(t):
    old = _Client()
    new = _Client()
    t._clients[_key(t)] = old
    # The roster lookup runs on old; a reconnect lands during it, so the
    # creation-state lookup that follows runs on new.
    old.during = lambda: t._clients.__setitem__(_key(t), new)

    kind, count = await t._resolve_room_shape(ROOM, _cred())
    assert (kind, count) == (None, 2)
    assert (old.joined_calls, old.state_calls) == (1, 0)
    assert (new.joined_calls, new.state_calls) == (0, 1)
    assert old.closed == 1
    assert new.closed == 0


@pytest.mark.asyncio
async def test_room_shape_without_a_client_is_an_unverified_lookup(t):
    kind, count = await t._resolve_room_shape(ROOM, _cred())
    assert (kind, count) == (None, 0)
    assert ROOM not in t._room_roster_authoritative
    assert ROOM not in t._room_kind_checked  # asked again next time


# ────────────────────────────────────────────────────────────────────
# Voice stamp
# ────────────────────────────────────────────────────────────────────


def _msg(raw: Optional[dict]) -> ParsedMessage:
    return ParsedMessage(
        message_id="$e",
        chat_id=ROOM,
        sender_id=USER_MXID,
        sender_name=USER_MXID,
        content="hi",
        chat_type=ChatType.PRIVATE,
        timestamp_ms=1,
        raw=raw,  # type: ignore[arg-type]
    )


def test_voice_stamp_helpers():
    stamp = {"rtc_session_id": "s1", "turn_id": "t1"}
    assert rtc_voice_stamp(_msg({"rtc_voice": stamp})) is stamp
    assert is_voice_turn(_msg({"rtc_voice": stamp})) is True
    assert is_voice_turn(_msg({"rtc_voice": {}})) is False
    assert is_voice_turn(_msg({"body": "hi"})) is False
    assert rtc_voice_stamp(SimpleNamespace()) is None  # no raw at all
    assert is_voice_turn(SimpleNamespace(raw=None)) is False
