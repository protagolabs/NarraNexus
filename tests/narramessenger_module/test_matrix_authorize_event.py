"""
@file_name: test_matrix_authorize_event.py
@date: 2026-07-02
@description: MatrixTrigger — Narra authorize-event gate.

Locks the mandatory per-event authorization contract from
NarraMessenger's Direct Matrix setup guide (``Matrix Authorize Event``
section):

- MUST call POST /api/agent-runtime/matrix/authorize-event before
  reading history, writing memory, invoking tools, calling model, or
  sending a Matrix reply. Applies to BOTH silent and full agent paths.
- Fail-closed on any non-``allow=True`` outcome: 4xx / 5xx / timeout /
  invalid JSON / missing allow field all → drop the event.
- 401 during pending bind is EXPECTED fail-closed behavior (owner has
  not called runtime-ready yet), NOT a permanent auth failure — the
  base's is_permanent_auth_failure only catches Matrix ``M_*`` codes,
  so this test also guards that the gate does not trip the base's
  credential-disable path.
- On deny + ``notice.send=true``, forward exactly ``notice.text`` as an
  m.notice reply; on deny + no notice, drop silently. Both are the only
  side effects allowed for a denied event.
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import aiohttp
import pytest

from xyz_agent_context.module.narramessenger_module._narramessenger_credential_manager import (
    NarramessengerCredential,
)
from xyz_agent_context.module.narramessenger_module import matrix_trigger as mt_mod
from xyz_agent_context.module.narramessenger_module.matrix_trigger import (
    MatrixTrigger,
    _AuthorizeVerdict,
)
from xyz_agent_context.schema.parsed_message import ChatType, ParsedMessage


ROOM = "!room:h"
AGENT_MXID = "@agent-abc:h"
SENDER_MXID = "@alice:h"
NARRA_BEARER = "narra-bearer-token"
NARRA_BASE = "https://api.netmind.chat"


def _cred() -> NarramessengerCredential:
    return NarramessengerCredential(
        agent_id="agent_x",
        bearer_token=NARRA_BEARER,
        backend_base_url=NARRA_BASE,
        matrix_user_id=AGENT_MXID,
    )


def _msg(body: str = "hi") -> ParsedMessage:
    return ParsedMessage(
        message_id="$evt1",
        chat_id=ROOM,
        sender_id=SENDER_MXID,
        sender_name=SENDER_MXID,
        content=body,
        chat_type=ChatType.PRIVATE,
        timestamp_ms=1,
        raw={"kind": "m.room.message.text"},
    )


# ────────────────────────────────────────────────────────────────────
# Fake aiohttp — record post kwargs, return canned response
# ────────────────────────────────────────────────────────────────────


class _FakePostResponse:
    def __init__(self, *, status: int, body: object, raise_json: bool = False):
        self.status = status
        self._body = body
        self._raise_json = raise_json

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return None

    async def json(self):
        if self._raise_json:
            # Use ValueError — matrix_trigger._authorize_event catches
            # both ContentTypeError and ValueError; ValueError is
            # cheaper to construct in tests than a real ContentTypeError
            # (which needs a live request_info).
            raise ValueError("not valid json")
        return self._body


class _FakeSession:
    """Records last POST and returns a pre-built response."""

    def __init__(
        self,
        *,
        status: int = 200,
        body: object = None,
        raise_exc: Exception | None = None,
        raise_json: bool = False,
    ):
        self.status = status
        self.body = body if body is not None else {}
        self.raise_exc = raise_exc
        self.raise_json = raise_json
        self.calls: list[tuple[str, dict, dict]] = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return None

    def post(self, url, *, json, headers):
        self.calls.append((url, dict(json), dict(headers)))
        if self.raise_exc is not None:
            raise self.raise_exc
        return _FakePostResponse(
            status=self.status, body=self.body, raise_json=self.raise_json
        )


@pytest.fixture
def fake_session(monkeypatch):
    holder: dict = {}

    def _factory(*a, **kw):
        s = holder.get("session") or _FakeSession()
        holder["session"] = s
        return s

    monkeypatch.setattr(mt_mod.aiohttp, "ClientSession", _factory)
    return holder


@pytest.fixture
def trigger():
    return MatrixTrigger()


# ────────────────────────────────────────────────────────────────────
# _authorize_event — direct unit tests
# ────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_allow_true_returns_allow_verdict(trigger, fake_session):
    fake_session["session"] = _FakeSession(
        status=200, body={"allow": True}
    )
    v = await trigger._authorize_event(_cred(), _msg(), mentioned=False)
    assert v.allow is True
    assert v.notice_send is False
    assert v.notice_text is None

    # Payload / headers sanity — bearer must match, path assembled correctly.
    _, s = list(fake_session.items())[0]
    url, payload, headers = s.calls[0]
    assert url == f"{NARRA_BASE}/api/agent-runtime/matrix/authorize-event"
    assert headers["Authorization"] == f"Bearer {NARRA_BEARER}"
    assert payload["roomId"] == ROOM
    assert payload["senderMatrixUserId"] == SENDER_MXID
    assert payload["mentioned"] is False
    # Members must include at least sender + our agent.
    assert AGENT_MXID in payload["memberMatrixUserIds"]
    assert SENDER_MXID in payload["memberMatrixUserIds"]


@pytest.mark.asyncio
async def test_deny_with_notice_forwards_text(trigger, fake_session):
    fake_session["session"] = _FakeSession(
        status=200,
        body={
            "allow": False,
            "notice": {"send": True, "text": "You are not allowed here."},
        },
    )
    v = await trigger._authorize_event(_cred(), _msg(), mentioned=True)
    assert v.allow is False
    assert v.notice_send is True
    assert v.notice_text == "You are not allowed here."


@pytest.mark.asyncio
async def test_deny_without_notice_silent_drop(trigger, fake_session):
    fake_session["session"] = _FakeSession(
        status=200,
        body={"allow": False, "notice": {"send": False}},
    )
    v = await trigger._authorize_event(_cred(), _msg(), mentioned=False)
    assert v.allow is False
    assert v.notice_send is False
    assert v.notice_text is None


@pytest.mark.asyncio
async def test_deny_when_allow_field_missing(trigger, fake_session):
    """A malformed body without an ``allow`` field must fail closed —
    not fall through to True because of Python truthiness. The check
    is ``data.get('allow') is True`` (identity), not ``bool(...)``.
    """
    fake_session["session"] = _FakeSession(
        status=200, body={"notice": {"send": False}}
    )
    v = await trigger._authorize_event(_cred(), _msg(), mentioned=False)
    assert v.allow is False


@pytest.mark.asyncio
async def test_deny_on_non_2xx_status(trigger, fake_session):
    fake_session["session"] = _FakeSession(status=500, body={})
    v = await trigger._authorize_event(_cred(), _msg(), mentioned=False)
    assert v.allow is False


@pytest.mark.asyncio
async def test_deny_on_401_pending_bind(trigger, fake_session):
    """401 while binding is still pending must fail-closed (per guide),
    NOT be treated as a permanent auth failure. This test locks that
    the gate itself does not signal the base to disable the credential
    — it just returns allow=False and moves on."""
    fake_session["session"] = _FakeSession(status=401, body={})
    v = await trigger._authorize_event(_cred(), _msg(), mentioned=False)
    assert v.allow is False


@pytest.mark.asyncio
async def test_deny_on_invalid_json_body(trigger, fake_session):
    fake_session["session"] = _FakeSession(
        status=200, body={}, raise_json=True
    )
    v = await trigger._authorize_event(_cred(), _msg(), mentioned=False)
    assert v.allow is False


@pytest.mark.asyncio
async def test_deny_on_transport_exception(trigger, fake_session):
    import asyncio
    fake_session["session"] = _FakeSession(
        raise_exc=asyncio.TimeoutError()
    )
    v = await trigger._authorize_event(_cred(), _msg(), mentioned=False)
    assert v.allow is False


@pytest.mark.asyncio
async def test_deny_when_credential_missing_bearer(trigger, fake_session):
    """A credential without bearer_token or backend_base_url short-
    circuits with a fail-closed deny AND does NOT make any HTTP call
    (which would otherwise crash on empty URL)."""
    fake_session["session"] = _FakeSession(status=200, body={"allow": True})
    bad_cred = NarramessengerCredential(
        agent_id="agent_x",
        bearer_token="",  # missing
        backend_base_url=NARRA_BASE,
        matrix_user_id=AGENT_MXID,
    )
    v = await trigger._authorize_event(bad_cred, _msg(), mentioned=False)
    assert v.allow is False
    assert fake_session["session"].calls == []


# ────────────────────────────────────────────────────────────────────
# _process_message flow — gate blocks routing on deny
# ────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_process_message_denied_no_notice_stops(trigger):
    """Deny + no notice → no super() call, no silent enqueue, no send."""
    trigger._clients[trigger._subscriber_key(_cred())] = SimpleNamespace()
    trigger._authorize_event = AsyncMock(return_value=_AuthorizeVerdict(allow=False))
    trigger._send_matrix_notice = AsyncMock()
    trigger._enqueue_silent = AsyncMock()
    trigger._classify = AsyncMock(return_value="dm")
    await trigger._process_message(_cred(), _msg())
    trigger._send_matrix_notice.assert_not_awaited()
    trigger._enqueue_silent.assert_not_awaited()
    trigger._classify.assert_not_awaited()


@pytest.mark.asyncio
async def test_process_message_denied_with_notice_forwards(trigger):
    """Deny + notice → m.notice sent, but classify/silent/full all skipped."""
    trigger._clients[trigger._subscriber_key(_cred())] = SimpleNamespace()
    trigger._authorize_event = AsyncMock(
        return_value=_AuthorizeVerdict(
            allow=False, notice_send=True, notice_text="Not allowed here."
        )
    )
    trigger._send_matrix_notice = AsyncMock()
    trigger._enqueue_silent = AsyncMock()
    trigger._classify = AsyncMock(return_value="dm")
    await trigger._process_message(_cred(), _msg())
    trigger._send_matrix_notice.assert_awaited_once()
    args = trigger._send_matrix_notice.await_args
    assert args.args[1] == ROOM
    assert args.args[2] == "Not allowed here."
    trigger._enqueue_silent.assert_not_awaited()
    trigger._classify.assert_not_awaited()


@pytest.mark.asyncio
async def test_process_message_allowed_falls_through_to_classify(trigger):
    """allow=True → classifier runs and mention flag is REUSED (not
    re-computed) via the ``mentioned`` kwarg."""
    trigger._clients[trigger._subscriber_key(_cred())] = SimpleNamespace()
    trigger._authorize_event = AsyncMock(return_value=_AuthorizeVerdict(allow=True))
    trigger._enqueue_silent = AsyncMock()
    trigger._classify = AsyncMock(return_value="group_silent")
    await trigger._process_message(_cred(), _msg())
    trigger._classify.assert_awaited_once()
    # The classifier was called with mentioned= kwarg reused from the
    # gate's payload — locks the "compute once" optimization.
    kwargs = trigger._classify.await_args.kwargs
    assert "mentioned" in kwargs
    trigger._enqueue_silent.assert_awaited_once()
