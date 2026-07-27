"""
@file_name: test_gateway_spend_reconciler.py
@author: Bin Liang
@date: 2026-07-27
@description: GatewaySpendReconciler — sums a finished free-tier run's real
token usage from the LiteLLM gateway (SpendLogs) and deducts it from the user's
quota. Uses a real in-memory SQLite ledger + a mocked gateway admin API.

Covers the bug it exists to fix: the proxied agent model reports 0 tokens via
the CLI, so agent usage never reached the quota. The reconciler is the piece
that makes the free-tier balance actually decrease with agent use.
"""
import json
from unittest.mock import MagicMock

import httpx
import pytest
import pytest_asyncio

from xyz_agent_context.agent_framework.providers.gateway_key_service import (
    GatewayKeyService,
)
from xyz_agent_context.agent_framework.quota_service import QuotaService
from xyz_agent_context.repository.gateway_session_key_repository import (
    GatewaySessionKeyRepository,
)
from xyz_agent_context.repository.quota_repository import QuotaRepository
from xyz_agent_context.services.gateway_spend_reconciler import (
    GatewaySpendReconciler,
)


class _Gateway:
    """Mocks /key/generate, /key/delete, /spend/logs. spend_rows can be a dict
    keyed by api_key (the key hash) so different runs return different usage."""

    def __init__(self, spend_rows=None):
        self.requests = []
        # spend_rows: {key_hash: [row, ...]}  OR  a flat list for any key.
        self._spend_rows = spend_rows or {}

    def handler(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content.decode() or "{}") if request.content else {}
        self.requests.append((request.url.path, body))
        if request.url.path.endswith("/key/generate"):
            # Give each run a distinct token hash so SpendLogs can differ.
            alias = body.get("key_alias", "sess_x")
            return httpx.Response(200, json={"key": f"sk-{alias}", "token": f"hash-{alias}"})
        if request.url.path.endswith("/key/delete"):
            return httpx.Response(200, json={"deleted": True})
        if request.url.path.endswith("/spend/logs"):
            api_key = request.url.params.get("api_key")
            rows = self._spend_rows
            if isinstance(rows, dict):
                rows = rows.get(api_key, [])
            return httpx.Response(200, json=rows)
        return httpx.Response(404, json={})


def _svc(db_client, gw):
    return GatewayKeyService(
        db_client,
        gateway_url="http://litellm:4000",
        admin_key="sk-master-admin",
        models=["agent-model", "helper-model"],
        transport=httpx.MockTransport(gw.handler),
    )


def _mk_sys_provider(enabled=True, initial=(10_000_000, 5_000_000)):
    m = MagicMock()
    m.is_enabled.return_value = enabled
    m.get_initial_quota.return_value = initial
    return m


@pytest_asyncio.fixture
async def quota(db_client):
    """A real QuotaService registered as the cost_tracker default; restored
    afterward so it doesn't leak into other tests."""
    repo = QuotaRepository(db_client)
    svc = QuotaService(repo=repo, system_provider=_mk_sys_provider(True))
    prev = getattr(QuotaService, "_default", None)
    QuotaService.set_default(svc)
    yield svc
    QuotaService._default = prev


# The reconciler filters revoked_at < (now - grace). A negative grace pushes the
# cutoff into the future so freshly-revoked rows in-test are eligible immediately.
_ELIGIBLE = GatewaySpendReconciler(flush_grace_seconds=-5)


async def _finish_run(svc, repo, user_id, run_id, agent_id="agt_1"):
    """Mint + revoke a run so it becomes a revoked, unmetered ledger row."""
    await svc.mint_session_key(user_id, agent_id=agent_id, run_id=run_id)
    await svc.revoke_session_key(run_id)


@pytest.mark.asyncio
async def test_reconcile_deducts_agent_usage_and_marks_metered(db_client, quota):
    await quota.init_for_user("usr_a")
    gw = _Gateway(spend_rows={
        "hash-sess_run1": [
            {"prompt_tokens": 83316, "completion_tokens": 202, "model": "DeepSeek-V4-Pro"},
        ],
    })
    svc = _svc(db_client, gw)
    repo = GatewaySessionKeyRepository(db_client)
    await _finish_run(svc, repo, "usr_a", "sess_run1")

    n = await _ELIGIBLE.reconcile_once(db=db_client, svc=svc)

    assert n == 1
    # Quota actually moved by the agent's REAL tokens.
    q = await QuotaRepository(db_client).get_by_user_id("usr_a")
    assert q.used_input_tokens == 83316
    assert q.used_output_tokens == 202
    # cost_records has the agent_loop row attributed to the user.
    rows = await db_client.get("cost_records", {"user_id": "usr_a"})
    assert len(rows) == 1
    assert rows[0]["call_type"] == "agent_loop"
    assert rows[0]["provider_source"] == "system"
    # Row stamped metered so it won't be charged again.
    run = await repo.get_by_id("sess_run1")
    assert run.metered_at is not None


@pytest.mark.asyncio
async def test_reconcile_is_idempotent(db_client, quota):
    await quota.init_for_user("usr_a")
    gw = _Gateway(spend_rows={
        "hash-sess_run1": [{"prompt_tokens": 1000, "completion_tokens": 10, "model": "m"}],
    })
    svc = _svc(db_client, gw)
    repo = GatewaySessionKeyRepository(db_client)
    await _finish_run(svc, repo, "usr_a", "sess_run1")

    assert await _ELIGIBLE.reconcile_once(db=db_client, svc=svc) == 1
    # Second pass sees metered_at set → nothing to do, no double charge.
    assert await _ELIGIBLE.reconcile_once(db=db_client, svc=svc) == 0

    q = await QuotaRepository(db_client).get_by_user_id("usr_a")
    assert q.used_input_tokens == 1000  # not 2000
    rows = await db_client.get("cost_records", {"user_id": "usr_a"})
    assert len(rows) == 1


@pytest.mark.asyncio
async def test_reconcile_skips_recent_runs_within_grace(db_client, quota):
    """A run revoked just now must NOT be metered by the default grace — its
    last SpendLog may not have flushed yet."""
    await quota.init_for_user("usr_a")
    gw = _Gateway(spend_rows={"hash-sess_fresh": [{"prompt_tokens": 5, "completion_tokens": 1}]})
    svc = _svc(db_client, gw)
    repo = GatewaySessionKeyRepository(db_client)
    await _finish_run(svc, repo, "usr_a", "sess_fresh")

    # Default grace (120s) → freshly revoked row is below the cutoff.
    default = GatewaySpendReconciler()
    assert await default.reconcile_once(db=db_client, svc=svc) == 0
    run = await repo.get_by_id("sess_fresh")
    assert run.metered_at is None  # still pending


@pytest.mark.asyncio
async def test_reconcile_leaves_run_unmetered_on_gateway_failure(db_client, quota):
    """If /spend/logs fails, the run stays unmetered so the next cycle retries
    — we never charge 0 or lose the usage."""
    await quota.init_for_user("usr_a")

    class _FailSpend(_Gateway):
        def handler(self, request):
            if request.url.path.endswith("/spend/logs"):
                return httpx.Response(503, json={"error": "boom"})
            return super().handler(request)

    gw = _FailSpend()
    svc = _svc(db_client, gw)
    repo = GatewaySessionKeyRepository(db_client)
    await _finish_run(svc, repo, "usr_a", "sess_run1")

    assert await _ELIGIBLE.reconcile_once(db=db_client, svc=svc) == 0
    run = await repo.get_by_id("sess_run1")
    assert run.metered_at is None
    q = await QuotaRepository(db_client).get_by_user_id("usr_a")
    assert q.used_input_tokens == 0


@pytest.mark.asyncio
async def test_reconcile_marks_zero_usage_run_without_deducting(db_client, quota):
    """An errored run with no gateway usage should be marked metered (so it's
    not re-scanned forever) but must not touch the quota."""
    await quota.init_for_user("usr_a")
    gw = _Gateway(spend_rows={"hash-sess_run1": []})  # no rows → (0, 0)
    svc = _svc(db_client, gw)
    repo = GatewaySessionKeyRepository(db_client)
    await _finish_run(svc, repo, "usr_a", "sess_run1")

    assert await _ELIGIBLE.reconcile_once(db=db_client, svc=svc) == 1
    run = await repo.get_by_id("sess_run1")
    assert run.metered_at is not None
    q = await QuotaRepository(db_client).get_by_user_id("usr_a")
    assert q.used_input_tokens == 0
    assert await db_client.get("cost_records", {"user_id": "usr_a"}) == []


@pytest.mark.asyncio
async def test_list_unmetered_revoked_excludes_active_and_metered(db_client):
    """Repository filter: only revoked + unmetered + old-enough rows surface."""
    repo = GatewaySessionKeyRepository(db_client)
    # Active run — never eligible.
    await repo.create(run_id="sess_active", user_id="u", agent_id="a", key_hash="h1")
    # Revoked + unmetered — eligible (with a permissive grace).
    await repo.create(run_id="sess_revoked", user_id="u", agent_id="a", key_hash="h2")
    await repo.mark_revoked("sess_revoked")
    # Revoked + already metered — excluded.
    await repo.create(run_id="sess_done", user_id="u", agent_id="a", key_hash="h3")
    await repo.mark_revoked("sess_done")
    await repo.mark_metered("sess_done")

    got = await repo.list_unmetered_revoked(older_than_seconds=-5)
    ids = {r.run_id for r in got}
    assert ids == {"sess_revoked"}
