"""
@file_name: test_gateway_key_service.py
@author: Bin Liang
@date: 2026-07-23
@description: GatewayKeyService — mint / revoke (+ per-user cleanup) of per-run
LiteLLM session keys, with the LiteLLM admin API mocked via httpx.MockTransport
and a real in-memory SQLite ledger (db_client fixture).
"""
import json

import httpx
import pytest
import pytest_asyncio

from xyz_agent_context.agent_framework.providers.gateway_key_service import (
    GatewayKeyService,
    MintedSessionKey,
)
from xyz_agent_context.repository.gateway_session_key_repository import (
    GatewaySessionKeyRepository,
)
from xyz_agent_context.schema.gateway_session_key_schema import (
    GatewaySessionKeyStatus,
)


class _Recorder:
    """Captures every admin-API request and replays canned responses."""

    def __init__(self, generate_response=None, fail_status=None, spend_rows=None):
        self.requests = []  # list of (path, parsed_json_body)
        self._generate_response = generate_response or {
            "key": "sk-minted-secret",
            "token": "hash-abc123",
        }
        self._fail_status = fail_status
        self._spend_rows = spend_rows if spend_rows is not None else []

    def handler(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content.decode() or "{}") if request.content else {}
        self.requests.append((request.url.path, body))
        if self._fail_status is not None:
            return httpx.Response(self._fail_status, json={"error": "boom"})
        if request.url.path.endswith("/key/generate"):
            return httpx.Response(200, json=self._generate_response)
        if request.url.path.endswith("/key/delete"):
            return httpx.Response(200, json={"deleted": True})
        if request.url.path.endswith("/spend/logs"):
            return httpx.Response(200, json=self._spend_rows)
        return httpx.Response(404, json={})


def _svc(db_client, recorder, **kw):
    return GatewayKeyService(
        db_client,
        gateway_url="http://litellm:4000",
        admin_key="sk-master-admin",
        models=["agent-model", "helper-model"],
        transport=httpx.MockTransport(recorder.handler),
        **kw,
    )


@pytest_asyncio.fixture
async def repo(db_client):
    return GatewaySessionKeyRepository(db_client)


@pytest.mark.asyncio
async def test_mint_returns_ticket_and_records_active_row(db_client, repo):
    rec = _Recorder()
    svc = _svc(db_client, rec)

    minted = await svc.mint_session_key("usr_a", agent_id="agt_1", run_id="sess_fixed")

    assert isinstance(minted, MintedSessionKey)
    assert minted.key == "sk-minted-secret"
    assert minted.run_id == "sess_fixed"
    assert minted.base_url == "http://litellm:4000"

    row = await repo.get_by_id("sess_fixed")
    assert row is not None
    assert row.status is GatewaySessionKeyStatus.ACTIVE
    assert row.user_id == "usr_a"
    assert row.agent_id == "agt_1"
    # Non-secret token hash stored; the usable secret is NEVER persisted.
    assert row.key_hash == "hash-abc123"


@pytest.mark.asyncio
async def test_mint_uses_run_id_as_alias_and_sets_no_ttl(db_client):
    """铁律 #14: a wall-clock TTL would guillotine hours-long runs. The mint
    payload must carry no `duration`, and revoke must key on the alias."""
    rec = _Recorder()
    svc = _svc(db_client, rec)

    await svc.mint_session_key("usr_a", run_id="sess_ttlcheck")

    gen_path, gen_body = rec.requests[0]
    assert gen_path.endswith("/key/generate")
    assert gen_body["key_alias"] == "sess_ttlcheck"
    assert "duration" not in gen_body  # no expiry — lifecycle-bounded instead
    assert gen_body["metadata"]["user_id"] == "usr_a"
    # No budget configured → no gateway cap sent.
    assert "max_budget" not in gen_body


@pytest.mark.asyncio
async def test_mint_sets_max_budget_when_configured(db_client):
    """#3: the per-key USD ceiling must reach the gateway so a leaked ticket's
    blast radius is really bounded (not just the master key hidden)."""
    rec = _Recorder()
    svc = _svc(db_client, rec, key_max_budget_usd=5.0)

    await svc.mint_session_key("usr_a", run_id="sess_budget")

    _, gen_body = rec.requests[0]
    assert gen_body["max_budget"] == 5.0


@pytest.mark.asyncio
async def test_mint_returns_none_when_gateway_errors_and_writes_no_row(db_client, repo):
    rec = _Recorder(fail_status=503)
    svc = _svc(db_client, rec)

    minted = await svc.mint_session_key("usr_a", run_id="sess_fail")

    assert minted is None  # caller treats as system-tier-unavailable, no fallback
    assert await repo.get_by_id("sess_fail") is None


@pytest.mark.asyncio
async def test_revoke_deletes_by_alias_and_marks_row(db_client, repo):
    rec = _Recorder()
    svc = _svc(db_client, rec)
    await svc.mint_session_key("usr_a", run_id="sess_rev")

    await svc.revoke_session_key("sess_rev")

    del_calls = [b for p, b in rec.requests if p.endswith("/key/delete")]
    assert del_calls == [{"key_aliases": ["sess_rev"]}]
    row = await repo.get_by_id("sess_rev")
    assert row.status is GatewaySessionKeyStatus.REVOKED
    assert row.revoked_at is not None


@pytest.mark.asyncio
async def test_revoke_never_raises_when_gateway_down(db_client, repo):
    # Mint with a healthy gateway, then swap in a failing one for revoke.
    ok = _Recorder()
    svc_ok = _svc(db_client, ok)
    await svc_ok.mint_session_key("usr_a", run_id="sess_rev2")

    bad = _Recorder(fail_status=500)
    svc_bad = _svc(db_client, bad)
    # Must not raise even though the gateway delete fails.
    await svc_bad.revoke_session_key("sess_rev2")
    # Ledger still flips to revoked so the reaper won't chase it forever.
    row = await repo.get_by_id("sess_rev2")
    assert row.status is GatewaySessionKeyStatus.REVOKED


@pytest.mark.asyncio
async def test_revoke_all_for_user_only_touches_that_user(db_client, repo):
    rec = _Recorder()
    svc = _svc(db_client, rec)
    await svc.mint_session_key("usr_a", run_id="sess_a1")
    await svc.mint_session_key("usr_a", run_id="sess_a2")
    await svc.mint_session_key("usr_b", run_id="sess_b1")

    n = await svc.revoke_all_for_user("usr_a")

    assert n == 2
    assert (await repo.get_by_id("sess_a1")).status is GatewaySessionKeyStatus.REVOKED
    assert (await repo.get_by_id("sess_a2")).status is GatewaySessionKeyStatus.REVOKED
    # Another user's live key must be untouched.
    assert (await repo.get_by_id("sess_b1")).status is GatewaySessionKeyStatus.ACTIVE


@pytest.mark.asyncio
async def test_fetch_run_usage_sums_spend_rows(db_client):
    """The gateway is the authoritative token source for proxied agent runs
    (the CLI reports 0). fetch_run_usage sums prompt/completion across the
    key's SpendLog rows and surfaces the model."""
    rows = [
        {"prompt_tokens": 100, "completion_tokens": 20, "model": "DeepSeek-V4-Pro"},
        {"prompt_tokens": 50, "completion_tokens": 5, "model": "DeepSeek-V4-Pro"},
    ]
    rec = _Recorder(spend_rows=rows)
    svc = _svc(db_client, rec)

    usage = await svc.fetch_run_usage("hash-abc123")

    assert usage == (150, 25, "DeepSeek-V4-Pro")
    # Queried by api_key = the key hash.
    path, _ = rec.requests[-1]
    assert path.endswith("/spend/logs")


@pytest.mark.asyncio
async def test_fetch_run_usage_returns_none_on_error(db_client):
    """A gateway failure must return None (not (0,0)) so the reconciler leaves
    the run unmetered and retries next cycle instead of charging zero."""
    rec = _Recorder(fail_status=503)
    svc = _svc(db_client, rec)
    assert await svc.fetch_run_usage("hash-x") is None


@pytest.mark.asyncio
async def test_fetch_run_usage_none_key_is_none(db_client):
    rec = _Recorder()
    svc = _svc(db_client, rec)
    assert await svc.fetch_run_usage("") is None


@pytest.mark.asyncio
async def test_from_env_none_without_config(db_client, monkeypatch):
    monkeypatch.delenv("SYSTEM_DEFAULT_LLM_GATEWAY_URL", raising=False)
    monkeypatch.delenv("SYSTEM_DEFAULT_LLM_GATEWAY_ADMIN_KEY", raising=False)
    assert GatewayKeyService.from_env(db_client) is None


@pytest.mark.asyncio
async def test_from_env_builds_when_configured(db_client, monkeypatch):
    monkeypatch.setenv("SYSTEM_DEFAULT_LLM_GATEWAY_URL", "http://litellm:4000")
    monkeypatch.setenv("SYSTEM_DEFAULT_LLM_GATEWAY_ADMIN_KEY", "sk-admin")
    svc = GatewayKeyService.from_env(db_client)
    assert svc is not None
    assert svc.is_enabled() is True
