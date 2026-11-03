"""
@file_name: test_matrix_bind_service.py
@date: 2026-07-02
@description: NarraMessenger Direct Matrix bind flow (Commit 6).

Locks the contract discovered from the 2026-07-02 setup-guide probe:

- The setup guide's ``Matrix Connection Details`` table row reveals the
  Matrix access token (``syt_...``); ``_parse_setup_guide`` MUST extract it
  in addition to bearer / homeserver / user_id.
- ``do_bind`` calls ``POST /bind-agent/runtime-ready?token=<bind_token>``
  (NOT the old ``POST /api/agent-gateway/connect``) and stores the response
  ``roomId`` as ``bind_room_id``.
- The stored credential row has ``connection_mode='matrix'`` and populates
  ``matrix_access_token`` from the guide, so MatrixTrigger's credential
  watcher picks it up.
- If the guide reveals bearer but not matrix_access_token,
  ``do_bind`` returns a clean error rather than persisting a half-provisioned
  Matrix-mode row (which MatrixTrigger.connect would reject with
  ValueError anyway, only more noisily).
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from narranexus_plugins.narramessenger_module import (
    _narramessenger_service as svc,
)
from narranexus_plugins.narramessenger_module._narramessenger_credential_manager import (
    NarramessengerCredentialManager,
)


# ────────────────────────────────────────────────────────────────────
# _parse_setup_guide — regex extraction
# ────────────────────────────────────────────────────────────────────


CONNECTED_GUIDE_FRAGMENT = """
# Narra Messenger Agent Integration

> **Bind Flow Status**: `waiting_connection`

## Your Agent Identity
- **Agent ID (Principal ID)**: 00000000-0000-0000-0000-000000000000
- **Matrix User ID**: @agent-testonly:matrix.netmind.chat

## Authentication
All requests use the header:
```
Authorization: Bearer deadbeefdeadbeefdeadbeefdeadbeefdeadbeefdeadbeefdeadbeefdeadbeef
```

## Matrix Connection Details
| Field | Value |
|-------|-------|
| Homeserver URL | `https://matrix.netmind.chat` |
| User ID | `@agent-testonly:matrix.netmind.chat` |
| Access Token | `syt_FAKETESTtoken_do_not_use_00000000` |
"""


def test_parser_extracts_all_four_credential_fields():
    out = svc._parse_setup_guide(CONNECTED_GUIDE_FRAGMENT)
    assert out["bearer"] == "deadbeefdeadbeefdeadbeefdeadbeefdeadbeefdeadbeefdeadbeefdeadbeef"
    assert out["matrix_access_token"] == "syt_FAKETESTtoken_do_not_use_00000000"
    assert out["homeserver"] == "https://matrix.netmind.chat"
    assert out["matrix_user_id"] == "@agent-testonly:matrix.netmind.chat"


def test_parser_returns_empty_when_bearer_not_yet_revealed():
    """Pre-``waiting_connection`` guide has none of these fields — the
    parser must return an empty-ish dict (no crashes, no fabricated
    values)."""
    early = """
    # Narra Messenger Agent Integration
    > **Bind Flow Status**: `awaiting_profile`
    Post your agent profile to advance.
    """
    out = svc._parse_setup_guide(early)
    assert "bearer" not in out
    assert "matrix_access_token" not in out


def test_parser_skips_syt_lookalikes_that_are_too_short():
    """The regex constrains to syt_ + 20+ alphanumerics. A stray
    ``syt_abc`` example in prose must not be picked up."""
    fragment = "Access Token: `syt_short`"
    out = svc._parse_setup_guide(fragment)
    assert "matrix_access_token" not in out


# ────────────────────────────────────────────────────────────────────
# do_bind — orchestration
# ────────────────────────────────────────────────────────────────────


class _FakeCredentialManager:
    """In-memory upsert recorder."""

    def __init__(self):
        self.upserts = []

    async def upsert(self, cred):
        self.upserts.append(cred)


@pytest.fixture
def fake_manager(monkeypatch):
    fake = _FakeCredentialManager()

    def _factory(_db):
        return fake

    monkeypatch.setattr(
        svc, "NarramessengerCredentialManager", _factory
    )
    return fake


@pytest.fixture
def stub_db():
    """A minimal DB stub that returns None from get_one so
    _agent_profile falls through to defaults ("NarraNexus Agent")."""
    async def _get_one(_table, _filters):
        return None
    return SimpleNamespace(get_one=_get_one)


@pytest.fixture
def guide_scripts(monkeypatch):
    """Sequence of responses for _get_text / _post_json.

    Each test writes into these lists in order — the first _get_text
    call reads guide_scripts["guides"][0], etc. Failing to script
    enough responses raises IndexError, which points at the test.
    """
    state = {
        "guides": [],       # sequential responses to _get_text
        "post_calls": [],   # records (url, body, bearer) tuples
        "post_responses": [],  # sequential responses for _post_json
    }

    async def _fake_get(_session, url):
        return state["guides"].pop(0) if state["guides"] else ""

    async def _fake_post(_session, url, body, bearer=""):
        state["post_calls"].append((url, body, bearer))
        return state["post_responses"].pop(0) if state["post_responses"] else {"ok": False, "error": "no scripted response"}

    monkeypatch.setattr(svc, "_get_text", _fake_get)
    monkeypatch.setattr(svc, "_post_json", _fake_post)
    return state


@pytest.mark.asyncio
async def test_do_bind_calls_runtime_ready_and_stores_matrix_creds(
    stub_db, fake_manager, guide_scripts
):
    """Happy path: guide already reveals bearer + access_token; do_bind
    calls /bind-agent/runtime-ready and stores connection_mode='matrix'."""
    guide_scripts["guides"] = [CONNECTED_GUIDE_FRAGMENT]
    guide_scripts["post_responses"] = [
        # runtime-ready succeeds
        {"ok": True, "data": {
            "status": "connected",
            "matrixUserId": "@agent-testonly:matrix.netmind.chat",
            "principalId": "00000000-0000-0000-0000-000000000000",
            "profileId": "prof-9",
            "roomId": "!bindroom:matrix.netmind.chat",
        }},
    ]

    result = await svc.do_bind(
        stub_db,
        agent_id="agent_x",
        bind_command="https://api.netmind.chat/yGO3BL/setup-guide.md",
    )

    assert result["success"] is True
    # Runtime-ready endpoint URL exact match — locks the "not
    # /api/agent-gateway/connect" contract.
    assert len(guide_scripts["post_calls"]) == 1
    url, body, _bearer = guide_scripts["post_calls"][0]
    assert url == "https://api.netmind.chat/bind-agent/runtime-ready?token=yGO3BL"
    # Platform tag: NarraMessenger marks the agent's origin from this call
    # (their side defaults to "default" when it is absent). Locked here
    # because a silently-dropped field looks identical to success on our
    # end — the regression only shows up in THEIR agent list.
    assert body == {"platform": svc.PLATFORM_TAG} == {"platform": "nexus"}

    # Credential upserted with Matrix-mode fields populated.
    assert len(fake_manager.upserts) == 1
    cred = fake_manager.upserts[0]
    assert cred.connection_mode == "matrix"
    assert cred.bearer_token == "deadbeefdeadbeefdeadbeefdeadbeefdeadbeefdeadbeefdeadbeefdeadbeef"
    assert cred.matrix_access_token == "syt_FAKETESTtoken_do_not_use_00000000"
    assert cred.matrix_user_id == "@agent-testonly:matrix.netmind.chat"
    assert cred.matrix_homeserver_url == "https://matrix.netmind.chat"
    assert cred.bind_room_id == "!bindroom:matrix.netmind.chat"
    assert cred.nexus_profile_id == "prof-9"
    assert cred.enabled is True


@pytest.mark.asyncio
async def test_do_bind_leaves_profile_id_empty_when_absent(
    stub_db, fake_manager, guide_scripts
):
    """When the runtime-ready response has no ``profileId`` (current prod
    behaviour — the platform doesn't return it yet), the stored credential
    keeps ``nexus_profile_id`` as the empty-string default rather than
    "None" or a KeyError."""
    guide_scripts["guides"] = [CONNECTED_GUIDE_FRAGMENT]
    guide_scripts["post_responses"] = [
        {"ok": True, "data": {
            "status": "connected",
            "matrixUserId": "@agent-testonly:matrix.netmind.chat",
            "principalId": "00000000-0000-0000-0000-000000000000",
            "roomId": "!bindroom:matrix.netmind.chat",
        }},
    ]

    result = await svc.do_bind(
        stub_db,
        agent_id="agent_x",
        bind_command="https://api.netmind.chat/yGO3BL/setup-guide.md",
    )

    assert result["success"] is True
    assert len(fake_manager.upserts) == 1
    cred = fake_manager.upserts[0]
    assert cred.nexus_profile_id == ""


@pytest.mark.asyncio
async def test_do_bind_rejects_when_matrix_access_token_missing(
    stub_db, fake_manager, guide_scripts
):
    """If the guide has bearer + homeserver + user_id but no
    Matrix access token, refuse to persist a half-provisioned row —
    MatrixTrigger.connect would raise ValueError on empty access_token
    and the base would disable the credential, which is a worse UX
    than telling the owner to re-bind."""
    fragment_missing_access = """
    ## Authentication
    ```
    Authorization: Bearer deadbeefdeadbeefdeadbeefdeadbeefdeadbeefdeadbeefdeadbeefdeadbeef
    ```

    ## Matrix Connection Details
    | Field | Value |
    |-------|-------|
    | Homeserver URL | `https://matrix.netmind.chat` |
    | User ID | `@agent-testonly:matrix.netmind.chat` |
    """
    guide_scripts["guides"] = [fragment_missing_access]
    guide_scripts["post_responses"] = [
        {"ok": True, "data": {"status": "connected", "roomId": "!x:h"}},
    ]
    result = await svc.do_bind(
        stub_db,
        agent_id="agent_x",
        bind_command="https://api.netmind.chat/yGO3BL/setup-guide.md",
    )
    assert result["success"] is False
    assert "Matrix access token missing" in result["error"]
    # No credential written.
    assert fake_manager.upserts == []


@pytest.mark.asyncio
async def test_do_bind_surfaces_runtime_ready_failure(
    stub_db, fake_manager, guide_scripts
):
    """A 4xx from runtime-ready must return a user-visible error rather
    than persisting anything. Nothing worse than showing a green
    "bound!" toast while the backend has no live binding."""
    guide_scripts["guides"] = [CONNECTED_GUIDE_FRAGMENT]
    guide_scripts["post_responses"] = [
        {"ok": False, "status": 409, "error": "TOKEN_ALREADY_USED"},
    ]
    result = await svc.do_bind(
        stub_db,
        agent_id="agent_x",
        bind_command="https://api.netmind.chat/yGO3BL/setup-guide.md",
    )
    assert result["success"] is False
    assert "runtime-ready failed" in result["error"]
    assert fake_manager.upserts == []


@pytest.mark.asyncio
async def test_do_bind_first_call_reports_profile_before_runtime_ready(
    stub_db, fake_manager, guide_scripts
):
    """If the initial guide fetch shows no bearer yet, do_bind should
    POST report-profile, refetch the guide, then call runtime-ready.
    This locks the "advance the state machine before demanding creds"
    behaviour so the bind flow doesn't fail on a fresh session."""
    early = "# Narra Messenger Agent Integration\n> **Bind Flow Status**: `awaiting_profile`\n"
    guide_scripts["guides"] = [early, CONNECTED_GUIDE_FRAGMENT]
    guide_scripts["post_responses"] = [
        # report-profile
        {"ok": True, "data": {}},
        # runtime-ready
        {"ok": True, "data": {
            "status": "connected",
            "roomId": "!bindroom:matrix.netmind.chat",
        }},
    ]

    result = await svc.do_bind(
        stub_db,
        agent_id="agent_x",
        bind_command="https://api.netmind.chat/yGO3BL/setup-guide.md",
    )
    assert result["success"] is True
    assert len(guide_scripts["post_calls"]) == 2
    # First call = report-profile, second = runtime-ready.
    assert "/bind-agent/report-profile" in guide_scripts["post_calls"][0][0]
    assert "/bind-agent/runtime-ready" in guide_scripts["post_calls"][1][0]
    # report-profile keeps its own contract (name+bio only); the platform
    # tag rides on runtime-ready, which is the one call every bind makes —
    # report-profile is skipped whenever the bearer is already revealed.
    assert set(guide_scripts["post_calls"][0][1]) == {"name", "bio"}
    assert guide_scripts["post_calls"][1][1] == {"platform": "nexus"}


# ────────────────────────────────────────────────────────────────────
# SSRF egress gate at bind time (cloud): the pasted link's host and the
# homeserver parsed from the guide are both attacker-influenced.
# ────────────────────────────────────────────────────────────────────


_BIND_DNS = {
    "api.netmind.chat": ["104.18.1.1"],
    "matrix.netmind.chat": ["104.18.1.2"],
    "quota-api": ["172.20.0.7"],
    "evil-homeserver.example": ["172.20.0.18"],
}


@pytest.fixture
def cloud_gate(monkeypatch):
    from narranexus.platform.utils import url_safety

    monkeypatch.setenv("NARRANEXUS_DEPLOYMENT_MODE", "cloud")

    async def _resolver(host, port):
        if host not in _BIND_DNS:
            raise OSError(f"unknown host {host}")
        return _BIND_DNS[host]

    monkeypatch.setattr(url_safety, "_default_resolver", _resolver)


@pytest.fixture
def no_session(monkeypatch):
    """Constructing an aiohttp session at all means a request could go out."""
    def _boom(*_a, **_k):
        raise AssertionError("no HTTP session may be opened for a refused bind link")

    monkeypatch.setattr(svc.aiohttp, "ClientSession", _boom)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "link",
    [
        "https://quota-api/tok123/setup-guide.md",          # docker service name
        "https://169.254.169.254/tok123/setup-guide.md",    # metadata IP
        "https://172.20.0.18/tok123/setup-guide.md",        # docker literal
        "https://unknown-host.invalid/tok123/setup-guide.md",  # DNS failure
    ],
)
async def test_cloud_internal_bind_link_refused_before_any_request(
    stub_db, fake_manager, guide_scripts, cloud_gate, no_session, link
):
    result = await svc.do_bind(stub_db, agent_id="agent_x", bind_command=link)

    assert result["success"] is False
    assert "172.20" not in result["error"] and "169.254" not in result["error"]
    assert guide_scripts["post_calls"] == []
    assert fake_manager.upserts == []


INTERNAL_HOMESERVER_GUIDE = CONNECTED_GUIDE_FRAGMENT.replace(
    "https://matrix.netmind.chat`", "https://evil-homeserver.example`"
)


@pytest.mark.asyncio
async def test_cloud_internal_homeserver_from_guide_refused_before_runtime_ready(
    stub_db, fake_manager, guide_scripts, cloud_gate
):
    guide_scripts["guides"] = [INTERNAL_HOMESERVER_GUIDE]
    guide_scripts["post_responses"] = [{"ok": True, "data": {"roomId": "!r:x"}}]

    result = await svc.do_bind(
        stub_db, agent_id="agent_x",
        bind_command="https://api.netmind.chat/tok123/setup-guide.md",
    )

    assert result["success"] is False
    assert "172.20" not in result["error"]
    # The remote bind is never completed and nothing is persisted.
    assert guide_scripts["post_calls"] == []
    assert fake_manager.upserts == []


@pytest.mark.asyncio
async def test_cloud_public_link_and_homeserver_still_bind(
    stub_db, fake_manager, guide_scripts, cloud_gate
):
    guide_scripts["guides"] = [CONNECTED_GUIDE_FRAGMENT]
    guide_scripts["post_responses"] = [{"ok": True, "data": {"roomId": "!r:x"}}]

    result = await svc.do_bind(
        stub_db, agent_id="agent_x",
        bind_command="https://api.netmind.chat/tok123/setup-guide.md",
    )

    assert result["success"] is True
    assert fake_manager.upserts[0].matrix_homeserver_url == "https://matrix.netmind.chat"


class _RecordingSession:
    def __init__(self, status=302):
        self.calls: list[tuple[str, str, dict]] = []
        self.status = status

    def _resp(self):
        outer = self

        class _R:
            status = outer.status

            async def __aenter__(self):
                return self

            async def __aexit__(self, *exc):
                return False

            async def text(self):
                return "REDIRECT-TARGET-BODY"

            async def json(self):
                return {}

        return _R()

    def get(self, url, **kw):
        self.calls.append(("GET", url, kw))
        return self._resp()

    def post(self, url, **kw):
        self.calls.append(("POST", url, kw))
        return self._resp()


@pytest.mark.asyncio
async def test_bind_http_helpers_do_not_follow_redirects():
    session = _RecordingSession(status=302)

    assert await svc._get_text(session, "https://api.netmind.chat/t/setup-guide.md") == ""
    out = await svc._post_json(session, "https://api.netmind.chat/bind-agent/x", {"a": 1})

    assert out["ok"] is False
    assert all(kw.get("allow_redirects") is False for _m, _u, kw in session.calls)


@pytest.mark.asyncio
async def test_refused_redirect_is_logged_by_status_without_location():
    """An upstream redirect collapses to "" (→ "token not found" for the user);
    the log must say it was a redirect so on-call is not sent after the token."""
    from loguru import logger

    messages: list[str] = []
    sink_id = logger.add(lambda m: messages.append(str(m)), level="WARNING")
    try:
        await svc._get_text(_RecordingSession(status=301), "https://api.netmind.chat/t/setup-guide.md")
    finally:
        logger.remove(sink_id)

    assert any("301" in m and "redirect" in m for m in messages)
    assert not any("REDIRECT-TARGET-BODY" in m for m in messages)


@pytest.mark.asyncio
async def test_local_mode_skips_the_bind_gate(
    stub_db, fake_manager, guide_scripts, monkeypatch
):
    """Local/desktop: a single trusted user — the gate is skipped, so even a
    LAN-looking link proceeds to fetch the guide (and the cloud fixture's
    resolver is not consulted at all)."""
    from narranexus.platform.utils import url_safety

    monkeypatch.setenv("NARRANEXUS_DEPLOYMENT_MODE", "local")

    async def _resolver_must_not_run(host, port):
        raise AssertionError("egress gate must not run in local mode")

    monkeypatch.setattr(url_safety, "_default_resolver", _resolver_must_not_run)
    guide_scripts["guides"] = [INTERNAL_HOMESERVER_GUIDE]
    guide_scripts["post_responses"] = [{"ok": True, "data": {"roomId": "!r:x"}}]

    result = await svc.do_bind(
        stub_db, agent_id="agent_x",
        bind_command="https://quota-api/tok123/setup-guide.md",
    )

    assert result["success"] is True
    assert fake_manager.upserts[0].matrix_homeserver_url == "https://evil-homeserver.example"


@pytest.mark.asyncio
async def test_bind_errors_never_echo_upstream_error_text(stub_db, fake_manager, guide_scripts):
    """Upstream JSON error/message strings are not concatenated into the
    user-facing error — status code plus a whitelisted code only."""
    guide_scripts["guides"] = [CONNECTED_GUIDE_FRAGMENT]
    guide_scripts["post_responses"] = [
        {"ok": False, "status": 500, "error": "SECRET upstream message from 172.20.0.7"},
    ]

    result = await svc.do_bind(
        stub_db, agent_id="agent_x",
        bind_command="https://api.netmind.chat/yGO3BL/setup-guide.md",
    )

    assert result["success"] is False
    assert "500" in result["error"]
    assert "SECRET" not in result["error"] and "172.20" not in result["error"]


@pytest.mark.asyncio
async def test_report_profile_error_never_echoes_upstream_text(stub_db, fake_manager, guide_scripts):
    early = "# guide\n> **Bind Flow Status**: `awaiting_profile`\n"
    guide_scripts["guides"] = [early]
    guide_scripts["post_responses"] = [
        {"ok": False, "status": 400, "error": "SECRET internal detail"},
    ]

    result = await svc.do_bind(
        stub_db, agent_id="agent_x",
        bind_command="https://api.netmind.chat/yGO3BL/setup-guide.md",
    )

    assert result["success"] is False
    assert "400" in result["error"]
    assert "SECRET" not in result["error"]


@pytest.mark.asyncio
async def test_step_failure_keeps_only_whitelisted_codes():
    assert svc._step_failure("runtime-ready", {"status": 409, "error": "WRONG_STATE"}) == (
        "runtime-ready failed (HTTP 409, WRONG_STATE)"
    )
    assert svc._step_failure("runtime-ready", {"status": 409, "error": "free text 10.0.0.1"}) == (
        "runtime-ready failed (HTTP 409)"
    )


@pytest.mark.asyncio
async def test_post_json_reports_whitelisted_code_only():
    class _S:
        def post(self, url, **kw):
            class _R:
                status = 409

                async def __aenter__(self):
                    return self

                async def __aexit__(self, *e):
                    return False

                async def json(self):
                    return {"error": "WRONG_STATE"}

            return _R()

    out = await svc._post_json(_S(), "https://api.netmind.chat/x", {})
    assert out == {"ok": False, "status": 409, "error": "WRONG_STATE"}


@pytest.mark.asyncio
async def test_cloud_rebinding_bind_link_is_refused_at_connect(stub_db, fake_manager, monkeypatch):
    # The gate sees a public answer, the session's dial would see the metadata
    # IP: the pinned connector refuses before any socket opens.
    import asyncio

    from narranexus.platform.utils import url_safety

    monkeypatch.setenv("NARRANEXUS_DEPLOYMENT_MODE", "cloud")
    answers = iter([["104.18.1.1"]])

    async def _rebinding(host, port):
        return next(answers, ["169.254.169.254"])

    monkeypatch.setattr(url_safety, "_default_resolver", _rebinding)
    dials = []

    async def _no_connect(self, sock, address):
        dials.append(address)
        raise OSError("test: sockets disabled")

    # aiohttp dials through aiohappyeyeballs -> loop.sock_connect.
    import asyncio.selector_events

    monkeypatch.setattr(asyncio.selector_events.BaseSelectorEventLoop, "sock_connect", _no_connect)

    result = await svc.do_bind(
        stub_db, agent_id="agent_x", bind_command="https://rebind.example/tok123/setup-guide.md"
    )

    assert result["success"] is False
    assert "public NarraMessenger host" in result["error"]
    assert "169.254" not in result["error"]
    assert dials == [] and fake_manager.upserts == []


# ────────────────────────────────────────────────────────────────────
# Bearer reveal wait (2026-11-02): report-profile advances the session, but
# the guide served right after it can still render the previous state. The
# bind used to read the guide once and fail ("credential isn't revealed
# yet") — every prod narramessenger bind after 10-30 failed this way.
# ────────────────────────────────────────────────────────────────────


@pytest.fixture
def recorded_sleeps(monkeypatch):
    slept: list[float] = []

    async def _fake_sleep(seconds):
        slept.append(seconds)

    monkeypatch.setattr(svc, "_sleep", _fake_sleep)
    return slept


_PENDING_GUIDE = "# Narra Messenger Agent Integration\n> **Bind Flow Status**: `profile_reported`\n"


@pytest.mark.asyncio
async def test_do_bind_waits_for_bearer_after_report_profile(
    stub_db, fake_manager, guide_scripts, recorded_sleeps
):
    early = "# Narra Messenger Agent Integration\n> **Bind Flow Status**: `awaiting_profile`\n"
    # First guide: pre-profile. Two re-fetches still lag, the third is connected.
    guide_scripts["guides"] = [early, _PENDING_GUIDE, _PENDING_GUIDE, CONNECTED_GUIDE_FRAGMENT]
    guide_scripts["post_responses"] = [
        {"ok": True, "data": {}},
        {"ok": True, "data": {"status": "connected", "roomId": "!bindroom:matrix.netmind.chat"}},
    ]

    result = await svc.do_bind(
        stub_db, agent_id="agent_x",
        bind_command="https://api.netmind.chat/yGO3BL/setup-guide.md",
    )

    assert result["success"] is True
    assert len(fake_manager.upserts) == 1
    assert len(recorded_sleeps) == 3  # stopped waiting as soon as the bearer showed up
    assert "/bind-agent/runtime-ready" in guide_scripts["post_calls"][-1][0]


@pytest.mark.asyncio
async def test_do_bind_names_flow_status_when_bearer_never_revealed(
    stub_db, fake_manager, guide_scripts, recorded_sleeps
):
    waiting = (
        "# Narra Messenger Agent Integration\n"
        "> **Bind Flow Status**: `awaiting_creator_confirmation`\n"
        "## Next Step\nAsk the creator to confirm.\n"
    )
    guide_scripts["guides"] = [_PENDING_GUIDE] + [waiting] * len(svc._REVEAL_DELAYS)
    guide_scripts["post_responses"] = [{"ok": True, "data": {}}]

    result = await svc.do_bind(
        stub_db, agent_id="agent_x",
        bind_command="https://api.netmind.chat/yGO3BL/setup-guide.md",
    )

    assert result["success"] is False
    assert "awaiting_creator_confirmation" in result["error"]
    assert "confirm" in result["error"]
    assert recorded_sleeps == list(svc._REVEAL_DELAYS)
    assert fake_manager.upserts == []
    # Never completed on the NarraMessenger side: runtime-ready not called.
    assert all("runtime-ready" not in url for url, _b, _t in guide_scripts["post_calls"])


@pytest.mark.asyncio
async def test_do_bind_skips_wait_when_first_guide_has_bearer(
    stub_db, fake_manager, guide_scripts, recorded_sleeps
):
    guide_scripts["guides"] = [CONNECTED_GUIDE_FRAGMENT]
    guide_scripts["post_responses"] = [
        {"ok": True, "data": {"status": "connected", "roomId": "!r:matrix.netmind.chat"}},
    ]

    result = await svc.do_bind(
        stub_db, agent_id="agent_x",
        bind_command="https://api.netmind.chat/yGO3BL/setup-guide.md",
    )

    assert result["success"] is True
    assert recorded_sleeps == []


def test_flow_status_is_parsed_as_bare_identifier():
    assert svc._parse_setup_guide(CONNECTED_GUIDE_FRAGMENT)["flow_status"] == "waiting_connection"
    # Free text after the status never rides along (it may be shown to the user).
    md = "> **Bind Flow Status**: `pending<script>alert(1)</script>`"
    assert svc._parse_setup_guide(md)["flow_status"] == "pending"
    assert "flow_status" not in svc._parse_setup_guide("no status here")


def test_guide_shape_carries_no_secret_values():
    shape = svc._guide_shape(CONNECTED_GUIDE_FRAGMENT)
    assert "deadbeef" not in shape
    assert "syt_" not in shape
    assert "flow_status=waiting_connection" in shape
    assert "bearer_word=True" in shape
    assert "Authentication" in shape
    assert svc._guide_shape("") == "empty"


@pytest.mark.asyncio
async def test_do_bind_timeout_returns_network_error(stub_db, fake_manager, monkeypatch):
    import asyncio

    async def _timeout(_session, _url):
        raise asyncio.TimeoutError()

    monkeypatch.setattr(svc, "_get_text", _timeout)

    result = await svc.do_bind(
        stub_db, agent_id="agent_x",
        bind_command="https://api.netmind.chat/yGO3BL/setup-guide.md",
    )

    assert result == {"success": False, "error": "network error reaching NarraMessenger: TimeoutError"}


@pytest.mark.asyncio
async def test_reveal_wait_stops_at_its_deadline(stub_db, fake_manager, guide_scripts, monkeypatch):
    """The cloud MCP bind reaches do_bind through a 20 s HTTP call: the wait
    must give up at _REVEAL_DEADLINE_S even when every sleep is slow."""
    clock = {"t": 0.0}
    reads = {"n": 0}
    GET_COST = 2.0  # a slow upstream: every guide read takes 2 s

    async def _sleep(seconds):
        clock["t"] += seconds

    async def _slow_get(_session, _url):
        reads["n"] += 1
        if reads["n"] > 1:  # the first read happens before the wait starts
            clock["t"] += GET_COST
        return _PENDING_GUIDE

    async def _post(_session, url, body, bearer=""):
        return {"ok": True, "data": {}}

    monkeypatch.setattr(svc, "_sleep", _sleep)
    monkeypatch.setattr(svc, "_monotonic", lambda: clock["t"])
    monkeypatch.setattr(svc, "_get_text", _slow_get)
    monkeypatch.setattr(svc, "_post_json", _post)

    result = await svc.do_bind(
        stub_db, agent_id="agent_x",
        bind_command="https://api.netmind.chat/yGO3BL/setup-guide.md",
    )

    assert result["success"] is False
    # Overshoot is at most the read in flight (bounded by wait_for in real runs).
    assert clock["t"] <= svc._REVEAL_DEADLINE_S + GET_COST
    # Stopped early: fewer re-reads than scheduled delays.
    assert reads["n"] - 1 < len(svc._REVEAL_DELAYS)


@pytest.mark.asyncio
async def test_wrong_state_report_profile_still_waits_for_bearer(
    stub_db, fake_manager, guide_scripts, recorded_sleeps
):
    guide_scripts["guides"] = [_PENDING_GUIDE, _PENDING_GUIDE, CONNECTED_GUIDE_FRAGMENT]
    guide_scripts["post_responses"] = [
        {"ok": False, "status": 409, "error": "WRONG_STATE"},
        {"ok": True, "data": {"status": "connected", "roomId": "!r:matrix.netmind.chat"}},
    ]

    result = await svc.do_bind(
        stub_db, agent_id="agent_x",
        bind_command="https://api.netmind.chat/yGO3BL/setup-guide.md",
    )

    assert result["success"] is True
    assert len(recorded_sleeps) == 2


@pytest.mark.asyncio
async def test_empty_refetch_keeps_last_status(stub_db, fake_manager, guide_scripts, recorded_sleeps):
    waiting = "> **Bind Flow Status**: `awaiting_creator_confirmation`\n"
    guide_scripts["guides"] = [_PENDING_GUIDE, waiting] + [""] * (len(svc._REVEAL_DELAYS) - 1)
    guide_scripts["post_responses"] = [{"ok": True, "data": {}}]

    result = await svc.do_bind(
        stub_db, agent_id="agent_x",
        bind_command="https://api.netmind.chat/yGO3BL/setup-guide.md",
    )

    assert result["success"] is False
    assert "awaiting_creator_confirmation" in result["error"]


def test_flow_status_ignores_prose_mentions():
    assert "flow_status" not in svc._parse_setup_guide("The Bind Flow Status is shown below.")


def test_guide_shape_drops_fenced_comments_and_token_shaped_headings():
    md = (
        "# Narra Messenger Agent Integration\n"
        "## Authentication\n"
        "```bash\n"
        "# export MATRIX_TOKEN=syt_FAKETESTtoken_do_not_use_00000000\n"
        "# curl https://api.netmind.chat/yGO3BL/setup-guide.md\n"
        "```\n"
        "## Token deadbeefdeadbeefdeadbeef\n"
    )
    shape = svc._guide_shape(md)
    assert "syt_" not in shape
    assert "yGO3BL" not in shape and "://" not in shape
    assert "deadbeef" not in shape
    assert "Authentication" in shape and "Narra Messenger Agent Integration" in shape


@pytest.mark.asyncio
async def test_failure_log_names_step_without_secrets(stub_db, fake_manager, guide_scripts):
    """The matrix-access-token branch logs the shape of a guide that DOES show
    the bearer: the log line must carry the step and none of the secrets."""
    from loguru import logger

    guide = (
        "> **Bind Flow Status**: `waiting_connection`\n"
        "## Authentication\n```\nAuthorization: Bearer deadbeefdeadbeefdeadbeefdeadbeef\n```\n"
        "```bash\n# curl https://api.netmind.chat/yGO3BL/setup-guide.md\n```\n"
    )
    guide_scripts["guides"] = [guide]
    guide_scripts["post_responses"] = [{"ok": True, "data": {"roomId": "!r:m"}}]
    lines: list[str] = []
    sink = logger.add(lambda m: lines.append(str(m)), level="WARNING")
    try:
        result = await svc.do_bind(
            stub_db, agent_id="agent_x",
            bind_command="https://api.netmind.chat/yGO3BL/setup-guide.md",
        )
    finally:
        logger.remove(sink)

    assert result["success"] is False
    joined = "\n".join(lines)
    assert "bind failed at matrix-access-token" in joined
    for secret in ("deadbeef", "yGO3BL", "://"):
        assert secret not in joined


@pytest.mark.asyncio
async def test_network_failure_log_never_carries_the_bind_token(stub_db, fake_manager, monkeypatch):
    """aiohttp's timeout message embeds the request URL, and the URL carries
    the bind token: the log must keep only the exception class."""
    import aiohttp
    from loguru import logger

    async def _timeout(_session, url):
        raise aiohttp.ConnectionTimeoutError(f"Connection timeout to host {url}")

    monkeypatch.setattr(svc, "_get_text", _timeout)
    lines: list[str] = []
    sink = logger.add(lambda m: lines.append(str(m)), level="DEBUG")
    try:
        result = await svc.do_bind(
            stub_db, agent_id="agent_x",
            bind_command="https://api.netmind.chat/yGO3BL/setup-guide.md",
        )
    finally:
        logger.remove(sink)

    assert result == {"success": False, "error": "network error reaching NarraMessenger: ConnectionTimeoutError"}
    joined = "\n".join(lines)
    assert "bind failed at network: ConnectionTimeoutError" in joined
    assert "yGO3BL" not in joined
