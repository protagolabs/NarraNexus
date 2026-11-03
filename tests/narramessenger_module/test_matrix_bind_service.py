"""
@file_name: test_matrix_bind_service.py
@date: 2026-07-02
@description: NarraMessenger Direct Matrix bind flow.

Locks the contract of the live setup guide (re-probed 2026-11-03):

- The pre-profile guide renders ``- **Status**: `waiting_profile```; after
  ``report-profile`` it renders ``> **Bind Flow Status**: `waiting_connection```.
- The guide never prints the runtime credentials. ``do_bind`` claims them once
  with ``POST /bind-agent/claim-credentials?token=<bind_token>``, whose JSON
  carries ``secret_token`` / ``matrix_user_id`` / ``matrix_access_token`` /
  ``homeserver_url``.
- ``do_bind`` then calls ``POST /bind-agent/runtime-ready?token=<bind_token>``
  and stores the response ``roomId`` as ``bind_room_id``, with
  ``connection_mode='matrix'`` so MatrixTrigger's credential watcher picks the
  row up.
- An incomplete or refused claim returns a clean error and persists nothing.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from narranexus_plugins.narramessenger_module import (
    _narramessenger_service as svc,
)


# ────────────────────────────────────────────────────────────────────
# Guides and claim payloads, shaped like the live ones (secrets are fakes)
# ────────────────────────────────────────────────────────────────────


PROFILE_GUIDE = """# Narra Messenger Bind Agent Guide

## Bind Session
- **Mode**: Matrix
- **Status**: `waiting_profile`
- **Guide Revision**: 0

## Current Action
- **Next Action**: Report profile
"""

CONNECTION_GUIDE = """# Narra Messenger Agent Integration

> **Bind Flow Status**: `waiting_connection`
> Runtime credentials are available.
## Your Agent Identity
- **Agent ID (Principal ID)**: 00000000-0000-0000-0000-000000000000
- **Matrix User ID**: @agent-testonly:matrix.netmind.chat

## Authentication
Narra API requests use `Authorization: Bearer <your Narra runtime bearer>`. This guide never prints the bearer.

## Narra CLI Local Handoff
```bash
NARRA_CLAIM_URL="https://api.netmind.chat/bind-agent/claim-credentials?token=yGO3BL"
```
"""

SECRET = "nxs_FAKEsecret_do_not_use_0000000000"
MATRIX_TOKEN = "syt_FAKETESTtoken_do_not_use_00000000"

CLAIM = {
    "secret_token": SECRET,
    "matrix_user_id": "@agent-testonly:matrix.netmind.chat",
    "matrix_access_token": MATRIX_TOKEN,
    "homeserver_url": "https://matrix.netmind.chat",
}

READY = {
    "status": "connected",
    "matrixUserId": "@agent-testonly:matrix.netmind.chat",
    "principalId": "00000000-0000-0000-0000-000000000000",
    "profileId": "prof-9",
    "roomId": "!bindroom:matrix.netmind.chat",
}

LINK = "https://api.netmind.chat/yGO3BL/setup-guide.md"
CLAIM_URL = "https://api.netmind.chat/bind-agent/claim-credentials?token=yGO3BL"
READY_URL = "https://api.netmind.chat/bind-agent/runtime-ready?token=yGO3BL"


def _ok(data):
    return {"ok": True, "data": data}


# ────────────────────────────────────────────────────────────────────
# Parsers
# ────────────────────────────────────────────────────────────────────


def test_flow_status_reads_both_guide_layouts():
    assert svc._flow_status(PROFILE_GUIDE) == "waiting_profile"
    assert svc._flow_status(CONNECTION_GUIDE) == "waiting_connection"
    assert svc._flow_status("") == ""


def test_flow_status_is_parsed_as_bare_identifier():
    # Free text after the status never rides along (it may be shown to the user).
    md = "> **Bind Flow Status**: `pending<script>alert(1)</script>`"
    assert svc._flow_status(md) == "pending"
    assert svc._flow_status("no status here") == ""


def test_flow_status_ignores_prose_mentions():
    assert svc._flow_status("The Bind Flow Status is shown below.") == ""


def test_parse_claim_requires_every_field():
    assert svc._parse_claim(CLAIM) == CLAIM
    for key in svc._CLAIM_FIELDS:
        assert svc._parse_claim({**CLAIM, key: ""}) is None
        assert svc._parse_claim({**CLAIM, key: None}) is None
        assert svc._parse_claim({k: v for k, v in CLAIM.items() if k != key}) is None
    assert svc._parse_claim({**CLAIM, "secret_token": 123}) is None


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
    call reads guide_scripts["guides"][0], etc. An unscripted read returns
    "" and an unscripted POST a failure, which points at the test.
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


@pytest.fixture
def recorded_sleeps(monkeypatch):
    slept: list[float] = []

    async def _fake_sleep(seconds):
        slept.append(seconds)

    monkeypatch.setattr(svc, "_sleep", _fake_sleep)
    return slept


def _urls(state):
    return [url for url, _b, _t in state["post_calls"]]


@pytest.mark.asyncio
async def test_do_bind_from_a_fresh_link_reports_profile_claims_then_completes(
    stub_db, fake_manager, guide_scripts, recorded_sleeps
):
    """The live flow: waiting_profile → report-profile → waiting_connection →
    claim-credentials → runtime-ready → credential row from the claim."""
    guide_scripts["guides"] = [PROFILE_GUIDE, CONNECTION_GUIDE]
    guide_scripts["post_responses"] = [
        _ok({"success": True, "status": "waiting_connection"}),  # report-profile
        _ok(CLAIM),                                               # claim-credentials
        _ok(READY),                                               # runtime-ready
    ]

    result = await svc.do_bind(stub_db, agent_id="agent_x", bind_command=LINK)

    assert result["success"] is True
    assert result["data"]["connection_mode"] == "matrix"
    assert _urls(guide_scripts) == [
        "https://api.netmind.chat/bind-agent/report-profile?token=yGO3BL",
        CLAIM_URL,
        READY_URL,
    ]
    calls = guide_scripts["post_calls"]
    # report-profile keeps its own contract (name+bio only).
    assert set(calls[0][1]) == {"name", "bio"}
    # The claim posts no body and no bearer (the token rides in the URL).
    assert calls[1][1] is None and calls[1][2] == ""
    # Platform tag: NarraMessenger marks the agent's origin from runtime-ready
    # (their side defaults to "default" when it is absent).
    assert calls[2][1] == {"platform": svc.PLATFORM_TAG} == {"platform": "nexus"}
    assert len(recorded_sleeps) == 1  # stopped waiting at the first claimable guide

    assert len(fake_manager.upserts) == 1
    cred = fake_manager.upserts[0]
    assert cred.connection_mode == "matrix"
    assert cred.bearer_token == SECRET
    assert cred.matrix_access_token == MATRIX_TOKEN
    assert cred.matrix_user_id == "@agent-testonly:matrix.netmind.chat"
    assert cred.matrix_homeserver_url == "https://matrix.netmind.chat"
    assert cred.backend_base_url == "https://api.netmind.chat"
    assert cred.bind_room_id == "!bindroom:matrix.netmind.chat"
    assert cred.nexus_principal_id == "00000000-0000-0000-0000-000000000000"
    assert cred.nexus_profile_id == "prof-9"
    assert cred.enabled is True


@pytest.mark.asyncio
async def test_do_bind_skips_report_profile_when_already_claimable(
    stub_db, fake_manager, guide_scripts, recorded_sleeps
):
    guide_scripts["guides"] = [CONNECTION_GUIDE]
    guide_scripts["post_responses"] = [_ok(CLAIM), _ok(READY)]

    result = await svc.do_bind(stub_db, agent_id="agent_x", bind_command=LINK)

    assert result["success"] is True
    assert _urls(guide_scripts) == [CLAIM_URL, READY_URL]
    assert recorded_sleeps == []


@pytest.mark.asyncio
async def test_do_bind_leaves_profile_id_empty_when_absent(
    stub_db, fake_manager, guide_scripts
):
    guide_scripts["guides"] = [CONNECTION_GUIDE]
    ready = {k: v for k, v in READY.items() if k != "profileId"}
    guide_scripts["post_responses"] = [_ok(CLAIM), _ok(ready)]

    result = await svc.do_bind(stub_db, agent_id="agent_x", bind_command=LINK)

    assert result["success"] is True
    assert fake_manager.upserts[0].nexus_profile_id == ""


@pytest.mark.asyncio
async def test_matrix_user_id_falls_back_to_the_claim(stub_db, fake_manager, guide_scripts):
    guide_scripts["guides"] = [CONNECTION_GUIDE]
    guide_scripts["post_responses"] = [_ok(CLAIM), _ok({"roomId": "!r:matrix.netmind.chat"})]

    result = await svc.do_bind(stub_db, agent_id="agent_x", bind_command=LINK)

    assert result["success"] is True
    assert fake_manager.upserts[0].matrix_user_id == "@agent-testonly:matrix.netmind.chat"


@pytest.mark.asyncio
@pytest.mark.parametrize("missing", list(svc._CLAIM_FIELDS))
async def test_incomplete_claim_is_refused_before_runtime_ready(
    stub_db, fake_manager, guide_scripts, missing
):
    """A claim without, e.g., the Matrix token must not complete the bind:
    MatrixTrigger.connect would reject the half-provisioned row and the base
    would disable the credential — worse than telling the owner to re-bind."""
    guide_scripts["guides"] = [CONNECTION_GUIDE]
    guide_scripts["post_responses"] = [_ok({**CLAIM, missing: ""}), _ok(READY)]

    result = await svc.do_bind(stub_db, agent_id="agent_x", bind_command=LINK)

    assert result["success"] is False
    assert "incomplete" in result["error"]
    assert "new bind link" in result["error"]
    assert READY_URL not in _urls(guide_scripts)
    assert fake_manager.upserts == []


@pytest.mark.asyncio
async def test_refused_claim_tells_the_user_to_make_a_new_link(
    stub_db, fake_manager, guide_scripts
):
    guide_scripts["guides"] = [CONNECTION_GUIDE]
    guide_scripts["post_responses"] = [
        {"ok": False, "status": 409, "error": "free text from 10.0.0.1"},
    ]

    result = await svc.do_bind(stub_db, agent_id="agent_x", bind_command=LINK)

    assert result["success"] is False
    assert "claim-credentials failed (HTTP 409)" in result["error"]
    assert "only once" in result["error"] and "new bind link" in result["error"]
    assert "10.0.0.1" not in result["error"]
    assert READY_URL not in _urls(guide_scripts)
    assert fake_manager.upserts == []


@pytest.mark.asyncio
async def test_do_bind_surfaces_runtime_ready_failure(
    stub_db, fake_manager, guide_scripts
):
    """A 4xx from runtime-ready must return a user-visible error rather
    than persisting anything. Nothing worse than showing a green
    "bound!" toast while the backend has no live binding."""
    guide_scripts["guides"] = [CONNECTION_GUIDE]
    guide_scripts["post_responses"] = [
        _ok(CLAIM),
        {"ok": False, "status": 409, "error": "TOKEN_ALREADY_USED"},
    ]

    result = await svc.do_bind(stub_db, agent_id="agent_x", bind_command=LINK)

    assert result["success"] is False
    assert "runtime-ready failed" in result["error"]
    assert fake_manager.upserts == []


@pytest.mark.asyncio
async def test_do_bind_waits_for_the_connection_status_after_report_profile(
    stub_db, fake_manager, guide_scripts, recorded_sleeps
):
    # Two re-fetches still lag behind report-profile; the third is claimable.
    guide_scripts["guides"] = [PROFILE_GUIDE, PROFILE_GUIDE, PROFILE_GUIDE, CONNECTION_GUIDE]
    guide_scripts["post_responses"] = [_ok({}), _ok(CLAIM), _ok(READY)]

    result = await svc.do_bind(stub_db, agent_id="agent_x", bind_command=LINK)

    assert result["success"] is True
    assert len(recorded_sleeps) == 3
    assert _urls(guide_scripts)[-2:] == [CLAIM_URL, READY_URL]


@pytest.mark.asyncio
async def test_do_bind_names_flow_status_when_never_claimable(
    stub_db, fake_manager, guide_scripts, recorded_sleeps
):
    waiting = (
        "# Narra Messenger Agent Integration\n"
        "> **Bind Flow Status**: `awaiting_creator_confirmation`\n"
        "## Next Step\nAsk the creator to confirm.\n"
    )
    guide_scripts["guides"] = [PROFILE_GUIDE] + [waiting] * len(svc._REVEAL_DELAYS)
    guide_scripts["post_responses"] = [_ok({})]

    result = await svc.do_bind(stub_db, agent_id="agent_x", bind_command=LINK)

    assert result["success"] is False
    assert "awaiting_creator_confirmation" in result["error"]
    assert "confirm" in result["error"]
    assert recorded_sleeps == list(svc._REVEAL_DELAYS)
    assert fake_manager.upserts == []
    # Neither the one-time claim nor the remote completion was attempted.
    assert CLAIM_URL not in _urls(guide_scripts)
    assert READY_URL not in _urls(guide_scripts)


@pytest.mark.asyncio
async def test_wrong_state_report_profile_still_waits(
    stub_db, fake_manager, guide_scripts, recorded_sleeps
):
    guide_scripts["guides"] = [PROFILE_GUIDE, PROFILE_GUIDE, CONNECTION_GUIDE]
    guide_scripts["post_responses"] = [
        {"ok": False, "status": 409, "error": "WRONG_STATE"},
        _ok(CLAIM),
        _ok(READY),
    ]

    result = await svc.do_bind(stub_db, agent_id="agent_x", bind_command=LINK)

    assert result["success"] is True
    assert len(recorded_sleeps) == 2


@pytest.mark.asyncio
async def test_empty_refetch_keeps_last_status(stub_db, fake_manager, guide_scripts, recorded_sleeps):
    waiting = "> **Bind Flow Status**: `awaiting_creator_confirmation`\n"
    guide_scripts["guides"] = [PROFILE_GUIDE, waiting] + [""] * (len(svc._REVEAL_DELAYS) - 1)
    guide_scripts["post_responses"] = [_ok({})]

    result = await svc.do_bind(stub_db, agent_id="agent_x", bind_command=LINK)

    assert result["success"] is False
    assert "awaiting_creator_confirmation" in result["error"]


# ────────────────────────────────────────────────────────────────────
# SSRF egress gate at bind time (cloud): the pasted link's host and the
# homeserver returned by that host's claim are both attacker-influenced.
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


INTERNAL_HOMESERVER_CLAIM = {**CLAIM, "homeserver_url": "https://evil-homeserver.example"}


@pytest.mark.asyncio
async def test_cloud_internal_homeserver_from_claim_refused_before_runtime_ready(
    stub_db, fake_manager, guide_scripts, cloud_gate
):
    guide_scripts["guides"] = [CONNECTION_GUIDE]
    guide_scripts["post_responses"] = [_ok(INTERNAL_HOMESERVER_CLAIM), _ok(READY)]

    result = await svc.do_bind(
        stub_db, agent_id="agent_x",
        bind_command="https://api.netmind.chat/tok123/setup-guide.md",
    )

    assert result["success"] is False
    assert "172.20" not in result["error"]
    # The remote bind is never completed and nothing is persisted.
    assert all("runtime-ready" not in url for url in _urls(guide_scripts))
    assert fake_manager.upserts == []


@pytest.mark.asyncio
async def test_cloud_public_link_and_homeserver_still_bind(
    stub_db, fake_manager, guide_scripts, cloud_gate
):
    guide_scripts["guides"] = [CONNECTION_GUIDE]
    guide_scripts["post_responses"] = [_ok(CLAIM), _ok(READY)]

    result = await svc.do_bind(
        stub_db, agent_id="agent_x",
        bind_command="https://api.netmind.chat/tok123/setup-guide.md",
    )

    assert result["success"] is True
    assert fake_manager.upserts[0].matrix_homeserver_url == "https://matrix.netmind.chat"


@pytest.mark.asyncio
async def test_local_mode_skips_the_bind_gate(
    stub_db, fake_manager, guide_scripts, monkeypatch
):
    """Local/desktop: a single trusted user — the gate is skipped, so even a
    LAN-looking link and homeserver proceed (and the resolver is never asked)."""
    from narranexus.platform.utils import url_safety

    monkeypatch.setenv("NARRANEXUS_DEPLOYMENT_MODE", "local")

    async def _resolver_must_not_run(host, port):
        raise AssertionError("egress gate must not run in local mode")

    monkeypatch.setattr(url_safety, "_default_resolver", _resolver_must_not_run)
    guide_scripts["guides"] = [CONNECTION_GUIDE]
    guide_scripts["post_responses"] = [_ok(INTERNAL_HOMESERVER_CLAIM), _ok(READY)]

    result = await svc.do_bind(
        stub_db, agent_id="agent_x",
        bind_command="https://quota-api/tok123/setup-guide.md",
    )

    assert result["success"] is True
    assert fake_manager.upserts[0].matrix_homeserver_url == "https://evil-homeserver.example"


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
async def test_claim_post_sends_no_body():
    """The claim endpoint is called like the guide's `curl -X POST <url>`."""
    session = _RecordingSession(status=200)

    await svc._post_json(session, CLAIM_URL, None)

    _m, _u, kw = session.calls[0]
    assert "json" not in kw and "data" not in kw


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
async def test_bind_errors_never_echo_upstream_error_text(stub_db, fake_manager, guide_scripts):
    """Upstream JSON error/message strings are not concatenated into the
    user-facing error — status code plus a whitelisted code only."""
    guide_scripts["guides"] = [CONNECTION_GUIDE]
    guide_scripts["post_responses"] = [
        _ok(CLAIM),
        {"ok": False, "status": 500, "error": "SECRET upstream message from 172.20.0.7"},
    ]

    result = await svc.do_bind(stub_db, agent_id="agent_x", bind_command=LINK)

    assert result["success"] is False
    assert "500" in result["error"]
    assert "SECRET" not in result["error"] and "172.20" not in result["error"]


@pytest.mark.asyncio
async def test_report_profile_error_never_echoes_upstream_text(stub_db, fake_manager, guide_scripts):
    guide_scripts["guides"] = [PROFILE_GUIDE]
    guide_scripts["post_responses"] = [
        {"ok": False, "status": 400, "error": "SECRET internal detail"},
    ]

    result = await svc.do_bind(stub_db, agent_id="agent_x", bind_command=LINK)

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
# Wait bounds, logging, and secret hygiene
# ────────────────────────────────────────────────────────────────────


def test_guide_shape_carries_no_secret_values():
    shape = svc._guide_shape(CONNECTION_GUIDE)
    assert "yGO3BL" not in shape and "://" not in shape
    assert "flow_status=waiting_connection" in shape
    assert "Authentication" in shape
    assert svc._guide_shape("") == "empty"


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
async def test_do_bind_timeout_returns_network_error(stub_db, fake_manager, monkeypatch):
    import asyncio

    async def _timeout(_session, _url):
        raise asyncio.TimeoutError()

    monkeypatch.setattr(svc, "_get_text", _timeout)

    result = await svc.do_bind(stub_db, agent_id="agent_x", bind_command=LINK)

    assert result == {"success": False, "error": "network error reaching NarraMessenger: TimeoutError"}


@pytest.mark.asyncio
async def test_status_wait_stops_at_its_deadline(stub_db, fake_manager, guide_scripts, monkeypatch):
    """The cloud MCP bind reaches do_bind through a 20 s HTTP call: the wait
    must give up at _REVEAL_DEADLINE_S even when every read is slow."""
    clock = {"t": 0.0}
    reads = {"n": 0}
    GET_COST = 2.0  # a slow upstream: every guide read takes 2 s

    async def _sleep(seconds):
        clock["t"] += seconds

    async def _slow_get(_session, _url):
        reads["n"] += 1
        if reads["n"] > 1:  # the first read happens before the wait starts
            clock["t"] += GET_COST
        return PROFILE_GUIDE

    async def _post(_session, url, body, bearer=""):
        return {"ok": True, "data": {}}

    monkeypatch.setattr(svc, "_sleep", _sleep)
    monkeypatch.setattr(svc, "_monotonic", lambda: clock["t"])
    monkeypatch.setattr(svc, "_get_text", _slow_get)
    monkeypatch.setattr(svc, "_post_json", _post)

    result = await svc.do_bind(stub_db, agent_id="agent_x", bind_command=LINK)

    assert result["success"] is False
    # Overshoot is at most the read in flight (bounded by wait_for in real runs).
    assert clock["t"] <= svc._REVEAL_DEADLINE_S + GET_COST
    # Stopped early: fewer re-reads than scheduled delays.
    assert reads["n"] - 1 < len(svc._REVEAL_DELAYS)


@pytest.fixture
def log_lines():
    from loguru import logger

    lines: list[str] = []
    sink = logger.add(lambda m: lines.append(str(m)), level="DEBUG")
    yield lines
    logger.remove(sink)


@pytest.mark.asyncio
async def test_successful_bind_never_logs_the_claimed_credentials(
    stub_db, fake_manager, guide_scripts, log_lines
):
    guide_scripts["guides"] = [CONNECTION_GUIDE]
    guide_scripts["post_responses"] = [_ok(CLAIM), _ok(READY)]

    result = await svc.do_bind(stub_db, agent_id="agent_x", bind_command=LINK)

    assert result["success"] is True
    joined = "\n".join(log_lines)
    assert "bound via Direct Matrix" in joined
    for secret in (SECRET, MATRIX_TOKEN, "yGO3BL"):
        assert secret not in joined


@pytest.mark.asyncio
async def test_incomplete_claim_log_names_fields_not_values(
    stub_db, fake_manager, guide_scripts, log_lines
):
    guide_scripts["guides"] = [CONNECTION_GUIDE]
    guide_scripts["post_responses"] = [_ok({**CLAIM, "matrix_access_token": ""})]

    await svc.do_bind(stub_db, agent_id="agent_x", bind_command=LINK)

    joined = "\n".join(log_lines)
    assert "bind failed at claim-credentials" in joined
    assert "secret_token" in joined  # names what arrived
    assert SECRET not in joined and "yGO3BL" not in joined


@pytest.mark.asyncio
async def test_unclaimable_log_names_step_without_secrets(
    stub_db, fake_manager, guide_scripts, recorded_sleeps, log_lines
):
    guide_scripts["guides"] = [PROFILE_GUIDE] * (len(svc._REVEAL_DELAYS) + 1)
    guide_scripts["post_responses"] = [_ok({})]

    result = await svc.do_bind(stub_db, agent_id="agent_x", bind_command=LINK)

    assert result["success"] is False
    assert "waiting_profile" in result["error"]
    joined = "\n".join(log_lines)
    assert "bind failed at await-connection" in joined
    assert "yGO3BL" not in joined and "://" not in joined


@pytest.mark.asyncio
async def test_network_failure_log_never_carries_the_bind_token(stub_db, fake_manager, monkeypatch, log_lines):
    """aiohttp's timeout message embeds the request URL, and the URL carries
    the bind token: the log must keep only the exception class."""
    import aiohttp

    async def _timeout(_session, url):
        raise aiohttp.ConnectionTimeoutError(f"Connection timeout to host {url}")

    monkeypatch.setattr(svc, "_get_text", _timeout)

    result = await svc.do_bind(stub_db, agent_id="agent_x", bind_command=LINK)

    assert result == {"success": False, "error": "network error reaching NarraMessenger: ConnectionTimeoutError"}
    joined = "\n".join(log_lines)
    assert "bind failed at network: ConnectionTimeoutError" in joined
    assert "yGO3BL" not in joined
