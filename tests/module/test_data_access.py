"""
@file_name: test_data_access.py
@author:
@date: 2026-08-10
@description: AgentDataStore seam — REAL parity between DirectStore and
HttpStore, the backend 200+success:false failure contract, the identity-header
forwarding whitelist, and the composition root's env gate.

Parity here means: the same scenario produces the same RETURN STRING from both
implementations. The fake backend below implements the actual route semantics
(backend/routes/agents/awareness.py with create_missing=false): failure is
HTTP 200 + {"success": false, "error": ...}; non-2xx only ever comes from the
transport/middleware layer (e.g. the Q6 identity 401).
"""
from __future__ import annotations

import asyncio

import httpx
import pytest

from xyz_agent_context.module.data_access import (
    DirectStore,
    HttpStore,
    get_agent_data_store,
)
from xyz_agent_context.module.data_access import store as st
from xyz_agent_context.module.data_access.factory import current_identity_headers

AGENT = "agent_39b2b72b823b"


# ---------------------------------------------------------------------------
# A fake backend implementing the REAL route contract, and a fake db world
# giving DirectStore the same known agents — parity = same scenario, same string.
# ---------------------------------------------------------------------------


def _patch_http(monkeypatch, handler):
    transport = httpx.MockTransport(handler)
    real_client = httpx.AsyncClient

    class PatchedClient(real_client):
        def __init__(self, **kw):
            kw.pop("transport", None)
            super().__init__(transport=transport, **kw)

    monkeypatch.setattr(httpx, "AsyncClient", PatchedClient)


def _http_store(monkeypatch, known_agents, upsert_ok=True, status=200):
    def handler(request: httpx.Request) -> httpx.Response:
        if status != 200:
            return httpx.Response(status, text="rejected")
        agent_id = request.url.path.split("/")[3]
        assert request.url.params.get("create_missing") == "false"
        if agent_id not in known_agents:
            return httpx.Response(200, json={
                "success": False,
                "error": f"No AwarenessModule instance found for agent_id={agent_id}",
            })
        if not upsert_ok:
            return httpx.Response(200, json={
                "success": False, "error": "Failed to update awareness",
            })
        return httpx.Response(200, json={"success": True, "awareness": "x"})

    _patch_http(monkeypatch, handler)
    return HttpStore("http://backend:8000")


def _direct_store(known_agents):
    store = DirectStore()

    async def fake_db():
        return object()

    async def fake_instance_id(db, agent_id):
        return f"aware_{agent_id}" if agent_id in known_agents else None

    store._db = fake_db  # type: ignore[method-assign]
    store._awareness_instance_id = fake_instance_id  # type: ignore[method-assign]
    return store


@pytest.fixture
def _upsert_spy(monkeypatch):
    calls: list[tuple[str, str]] = []

    class FakeAwarenessRepo:
        def __init__(self, db):
            pass

        async def upsert(self, instance_id, awareness):
            calls.append((instance_id, awareness))
            return True

    monkeypatch.setattr(
        "xyz_agent_context.repository.InstanceAwarenessRepository", FakeAwarenessRepo
    )
    return calls


# ---------------------------------------------------------------------------
# parity: same scenario, same string
# ---------------------------------------------------------------------------


def test_parity_success(monkeypatch, _upsert_spy):
    direct = _direct_store({AGENT})
    http = _http_store(monkeypatch, {AGENT})

    d = asyncio.run(direct.update_awareness(AGENT, "hello"))
    h = asyncio.run(http.update_awareness(AGENT, "hello"))
    assert d == h == st._AWARENESS_OK
    assert _upsert_spy == [(f"aware_{AGENT}", "hello")]


def test_parity_unknown_agent_is_an_error_on_both(monkeypatch, _upsert_spy):
    """The direct path treats an unknown agent_id as an ERROR — the Http path
    must too, NOT auto-create an instance for it (pre-review C2: the route's
    convenience auto-create is opted out via create_missing=false)."""
    direct = _direct_store(set())
    http = _http_store(monkeypatch, set())

    d = asyncio.run(direct.update_awareness(AGENT, "hello"))
    h = asyncio.run(http.update_awareness(AGENT, "hello"))
    assert d == h == st._no_instance_msg(AGENT)
    assert _upsert_spy == []


def test_http_backend_reported_failure_is_not_success(monkeypatch):
    """Pre-review C1: the route reports failure as HTTP 200 + success:false —
    a status-code-only check would call this 'updated successfully'."""
    http = _http_store(monkeypatch, {AGENT}, upsert_ok=False)
    out = asyncio.run(http.update_awareness(AGENT, "hello"))
    assert out == "Error: Failed to update awareness"


def test_http_transport_rejection_degrades_in_band(monkeypatch):
    """Pre-review I3: a middleware-layer 401/5xx (e.g. the Q6 identity gate
    before keys are provisioned) must come back as a readable string, never
    escape the MCP tool as an exception."""
    http = _http_store(monkeypatch, {AGENT}, status=401)
    out = asyncio.run(http.update_awareness(AGENT, "hello"))
    assert out == "Error: awareness backend rejected the call (401)"


def test_http_unreachable_backend_degrades_in_band(monkeypatch):
    def boom(request):
        raise httpx.ConnectError("no route to backend")

    _patch_http(monkeypatch, boom)
    store = HttpStore("http://backend:8000")
    out = asyncio.run(store.update_awareness(AGENT, "hello"))
    assert out == "Error: awareness backend unreachable"


def test_direct_resolves_instance_through_repository(monkeypatch, _upsert_spy):
    """The one piece of real logic DirectStore carries — instance resolution —
    exercised against the repository seam instead of being stubbed away
    (pre-review I1)."""

    class _Inst:
        instance_id = "aware_real"

    class FakeInstanceRepo:
        def __init__(self, db):
            pass

        async def get_by_agent(self, agent_id, module_class):
            assert module_class == "AwarenessModule"
            return [_Inst()] if agent_id == AGENT else []

    monkeypatch.setattr(
        "xyz_agent_context.repository.InstanceRepository", FakeInstanceRepo
    )

    async def fake_db():
        return object()

    store = DirectStore()
    store._db = fake_db  # type: ignore[method-assign]
    assert asyncio.run(store.update_awareness(AGENT, "hi")) == st._AWARENESS_OK
    assert asyncio.run(
        store.update_awareness("agent_nobody", "hi")
    ) == st._no_instance_msg("agent_nobody")


# ---------------------------------------------------------------------------
# identity-header forwarding whitelist
# ---------------------------------------------------------------------------


def test_identity_header_whitelist(monkeypatch):
    ambient = {
        "x-narranexus-agent-id": AGENT,
        "x-narranexus-team-id": "team_1",
        "authorization": "Bearer nx-agent:agent_a~chat",
        "cookie": "session=SECRET",
        "x-forwarded-for": "1.2.3.4",
        "content-type": "application/json",
    }
    monkeypatch.setattr(
        "xyz_agent_context.module._mcp_identity._ambient_headers", lambda: ambient
    )
    kept = current_identity_headers()
    assert kept == {
        "x-narranexus-agent-id": AGENT,
        "x-narranexus-team-id": "team_1",
        "authorization": "Bearer nx-agent:agent_a~chat",
    }


def test_identity_headers_empty_without_ambient_request(monkeypatch):
    monkeypatch.setattr(
        "xyz_agent_context.module._mcp_identity._ambient_headers", lambda: None
    )
    assert current_identity_headers() == {}


# ---------------------------------------------------------------------------
# composition root
# ---------------------------------------------------------------------------


def test_factory_default_is_direct(monkeypatch):
    monkeypatch.delenv("NARRANEXUS_BACKEND_URL", raising=False)
    assert isinstance(get_agent_data_store(), DirectStore)


def test_factory_cloud_is_http(monkeypatch):
    monkeypatch.setenv("NARRANEXUS_BACKEND_URL", "http://backend:8000")
    store = get_agent_data_store(identity_headers={"authorization": "Bearer nx-agent:a"})
    assert isinstance(store, HttpStore)
    assert store._base == "http://backend:8000"
    assert store._headers == {"authorization": "Bearer nx-agent:a"}


def test_http_store_sends_identity_headers(monkeypatch):
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["auth"] = request.headers.get("authorization")
        return httpx.Response(200, json={"success": True})

    _patch_http(monkeypatch, handler)
    store = HttpStore(
        "http://backend:8000",
        identity_headers={"Authorization": "Bearer nx-agent:a~chat"},
    )
    asyncio.run(store.update_awareness(AGENT, "x"))
    assert seen["auth"] == "Bearer nx-agent:a~chat"


# ---------------------------------------------------------------------------
# general_memory migration (PR-3): remember + memory_retain parity
# ---------------------------------------------------------------------------


class _Hit:
    """Minimal memory hit matching what memory.format_memory_hits reads."""

    @staticmethod
    def make(kind, text, tags=None, source_ref=None):
        h = _Hit()
        h.kind = kind
        rec = type("R", (), {})()
        rec.content_text = text
        rec.created_at = None
        rec.tags = tags or []
        rec.source_ref = source_ref
        h.record = rec
        return h


def _memory_direct(monkeypatch, hits=None, record_id="mem_1", retain_ok=True):
    """DirectStore with MemoryCoordinator/MemoryEngine stubbed. The stub records
    the ``limit`` it actually received in ``store._seen`` so a test can assert
    DirectStore clamped it the same way HttpStore does."""
    store = DirectStore()
    store._seen = {}  # type: ignore[attr-defined]

    async def fake_db():
        return object()

    store._db = fake_db  # type: ignore[method-assign]

    class FakeCoord:
        def __init__(self, engine):
            pass

        async def remember(self, query, limit):
            store._seen["limit"] = limit  # type: ignore[attr-defined]
            return hits or []

    class FakeEngine:
        def __init__(self, db, agent_id):
            pass

        async def retain(self, record):
            if not retain_ok:
                raise RuntimeError("db down")
            return type("Rec", (), {"record_id": record_id})()

    monkeypatch.setattr("xyz_agent_context.memory.MemoryCoordinator", FakeCoord)
    monkeypatch.setattr("xyz_agent_context.memory.MemoryEngine", FakeEngine)
    return store


def _memory_http(monkeypatch, remember_body=None, retain_body=None, seen=None):
    """HttpStore whose backend echoes what the REAL routes return. When
    ``remember_body`` is a list of hits, the handler renders it through the
    same ``format_memory_hits`` the route calls — so a passing parity assertion
    proves DirectStore and the route agree by construction, not because the
    test fed both the same literal."""
    from xyz_agent_context.memory import format_memory_hits

    def handler(request: httpx.Request) -> httpx.Response:
        if seen is not None:
            seen["limit"] = request.url.params.get("limit")
        if request.url.path.endswith("/memory/remember"):
            if isinstance(remember_body, list):
                q = request.url.params.get("query")
                return httpx.Response(200, json={
                    "success": True, "query": q,
                    "memories": format_memory_hits(remember_body),
                })
            return httpx.Response(200, json=remember_body)
        if request.url.path.endswith("/memory/retain"):
            return httpx.Response(200, json=retain_body)
        return httpx.Response(404)

    _patch_http(monkeypatch, handler)
    return HttpStore("http://backend:8000")


def test_remember_parity(monkeypatch):
    # Includes a source_ref hit so the one branching field in
    # format_memory_hits (`if r.source_ref: item["source"] = ...`) is exercised
    # on BOTH paths — the branch a divergent copy would silently drop.
    hits = [
        _Hit.make("observation", "the sky is blue", tags=["fact"]),
        _Hit.make("job", "ship the release", source_ref={"kind": "job", "id": "job_9"}),
    ]
    expected = {
        "success": True, "query": "sky",
        "memories": [
            {"kind": "observation", "memory": "the sky is blue", "when": None, "tags": ["fact"]},
            {"kind": "job", "memory": "ship the release", "when": None, "tags": [],
             "source": {"kind": "job", "id": "job_9"}},
        ],
    }
    direct = _memory_direct(monkeypatch, hits=hits)
    d = asyncio.run(direct.remember("agent_a", "sky", 15))
    assert d == expected

    # The http handler renders the SAME hits through the route's real
    # format_memory_hits, so this equality is a genuine cross-path check.
    http = _memory_http(monkeypatch, remember_body=hits)
    h = asyncio.run(http.remember("agent_a", "sky", 15))
    assert h == expected == d


def test_remember_limit_clamped_identically_on_both(monkeypatch):
    # limit=200 is a common LLM overreach ("recall everything"). Both paths
    # must clamp to the route's max=100 — local must not return a result the
    # cloud path would 422 on. This is the parity fix for the reviewer's
    # out-of-bounds finding.
    direct = _memory_direct(monkeypatch, hits=[])
    asyncio.run(direct.remember("agent_a", "sky", 200))
    assert direct._seen["limit"] == 100  # type: ignore[attr-defined]

    seen = {}
    http = _memory_http(monkeypatch, remember_body=[], seen=seen)
    out = asyncio.run(http.remember("agent_a", "sky", 200))
    assert out["success"] is True
    assert seen["limit"] == "100"  # reached the backend clamped, no 422


def test_remember_empty_and_overlong_query_rejected_on_both(monkeypatch):
    direct = _memory_direct(monkeypatch, hits=[])
    http = _memory_http(monkeypatch, remember_body=[])
    empty = {"success": False, "error": "query is empty", "memories": []}
    assert asyncio.run(direct.remember("agent_a", "  ", 15)) == empty
    assert asyncio.run(http.remember("agent_a", "", 15)) == empty  # rejected pre-send

    long_q = "a" * 513
    long_err = {"success": False, "error": "query too long (max 512 chars)", "memories": []}
    assert asyncio.run(direct.remember("agent_a", long_q, 15)) == long_err
    assert asyncio.run(http.remember("agent_a", long_q, 15)) == long_err


def test_retain_parity(monkeypatch):
    direct = _memory_direct(monkeypatch, record_id="mem_42")
    d = asyncio.run(direct.memory_retain("agent_a", "remember this", "MEMORY.md"))
    assert d == {"success": True, "record_id": "mem_42"}

    http = _memory_http(monkeypatch, retain_body={"success": True, "record_id": "mem_42"})
    h = asyncio.run(http.memory_retain("agent_a", "remember this", "MEMORY.md"))
    assert h == d


def test_retain_empty_content_is_rejected_on_both(monkeypatch):
    direct = _memory_direct(monkeypatch)
    http = _memory_http(monkeypatch)
    err = {"success": False, "error": "content is empty"}
    assert asyncio.run(direct.memory_retain("agent_a", "   ", "")) == err
    # HttpStore rejects before any request (no fake needed for the empty path).
    assert asyncio.run(http.memory_retain("agent_a", "", "")) == err


def test_retain_overlong_content_rejected_on_both(monkeypatch):
    # The "import a MEMORY.md slice" use case can exceed 64KB. Both paths must
    # reject identically instead of local writing / cloud silently 422-ing.
    direct = _memory_direct(monkeypatch)
    http = _memory_http(monkeypatch)
    big = "x" * 65537
    err = {"success": False, "error": "content too long (max 65536 chars)"}
    assert asyncio.run(direct.memory_retain("agent_a", big, "")) == err
    assert asyncio.run(http.memory_retain("agent_a", big, "")) == err  # rejected pre-send


def test_remember_http_transport_failure_degrades_in_band(monkeypatch):
    def boom(request):
        raise httpx.ConnectError("no route")

    _patch_http(monkeypatch, boom)
    store = HttpStore("http://backend:8000")
    out = asyncio.run(store.remember("agent_a", "x", 15))
    assert out["success"] is False
    assert "unreachable" in out["error"]
    assert out["memories"] == []  # tool's own failure shape


def test_remember_http_422_is_actionable(monkeypatch):
    # A 422 (route pydantic bound) must surface as an argument-fixable message,
    # not lumped with 401/502 "backend rejected" — else the model blind-retries.
    def bad_args(request):
        return httpx.Response(422, json={"detail": "validation error"})

    _patch_http(monkeypatch, bad_args)
    store = HttpStore("http://backend:8000")
    out = asyncio.run(store.remember("agent_a", "x", 15))
    assert out["success"] is False
    assert "invalid arguments" in out["error"]
    assert out["memories"] == []


def test_retain_http_401_degrades_in_band(monkeypatch):
    def gate(request):
        return httpx.Response(401, text="identity required")

    _patch_http(monkeypatch, gate)
    store = HttpStore("http://backend:8000")
    out = asyncio.run(store.memory_retain("agent_a", "fact", ""))
    assert out["success"] is False
    assert "401" in out["error"]


# ---------------------------------------------------------------------------
# social writes (PR-4): extract / merge / delete parity
# ---------------------------------------------------------------------------


class _Inst:
    def __init__(self, instance_id):
        self.instance_id = instance_id


def _social_direct(monkeypatch, *, has_instance=True, method_result=None,
                   search_result=None, recall_result=None, stats_result=None):
    """DirectStore with InstanceRepository + SocialNetworkModule stubbed. The
    fake module's methods return the matching *_result so a test pins the exact
    dict DirectStore forwards, and record (name, kwargs) in store._calls."""
    store = DirectStore()
    store._calls = {}  # type: ignore[attr-defined]

    async def fake_db():
        return object()

    store._db = fake_db  # type: ignore[method-assign]

    class FakeInstanceRepo:
        def __init__(self, db):
            pass

        async def get_by_agent(self, agent_id, module_class):
            return [_Inst("social_1")] if has_instance else []

    class FakeSocial:
        def __init__(self, agent_id, database_client, instance_id):
            pass

        async def extract_and_update_entity_info(self, **kw):
            store._calls["method"] = ("extract", kw)  # type: ignore[attr-defined]
            return method_result

        async def merge_entities(self, **kw):
            store._calls["method"] = ("merge", kw)  # type: ignore[attr-defined]
            return method_result

        async def delete_entity(self, **kw):
            store._calls["method"] = ("delete", kw)  # type: ignore[attr-defined]
            return method_result

        async def search_network(self, **kw):
            store._calls["method"] = ("search", kw)  # type: ignore[attr-defined]
            return search_result

        async def recall_entity_info(self, entity_id, instance_id):
            store._calls["method"] = ("recall", {"entity_id": entity_id, "instance_id": instance_id})  # type: ignore[attr-defined]
            return recall_result

        async def get_agent_stats(self, **kw):
            store._calls["method"] = ("stats", kw)  # type: ignore[attr-defined]
            return stats_result

    monkeypatch.setattr("xyz_agent_context.repository.InstanceRepository", FakeInstanceRepo)
    monkeypatch.setattr(
        "xyz_agent_context.module.social_network_module.SocialNetworkModule", FakeSocial
    )
    return store


def _social_http(monkeypatch, route_body, status=200):
    """HttpStore whose backend echoes a social write route's response shape
    (failures under the route family's ``error`` key, per _normalize_write_result)."""
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status, json=route_body)

    _patch_http(monkeypatch, handler)
    return HttpStore("http://backend:8000")


def test_social_extract_success_parity(monkeypatch):
    ok = {"success": True, "message": "Entity info updated successfully"}
    d = _social_direct(monkeypatch, method_result=ok)
    dr = asyncio.run(d.extract_entity_info(AGENT, "user_x", {"entity_name": "X"}, "merge"))
    assert dr == ok
    # The route leaves a success body untouched (_normalize_write_result only
    # rewrites failures), and so does HttpStore's _social_write_message.
    h = _social_http(monkeypatch, route_body=ok)
    hr = asyncio.run(h.extract_entity_info(AGENT, "user_x", {"entity_name": "X"}, "merge"))
    assert hr == ok == dr


def test_social_no_instance_parity(monkeypatch):
    from xyz_agent_context.module.social_network_module import social_instance_not_found_msg

    expected = {"success": False, "message": social_instance_not_found_msg(AGENT)}
    d = _social_direct(monkeypatch, has_instance=False)
    dr = asyncio.run(d.delete_entity(AGENT, "user_x"))
    assert dr == expected
    # The backend route returns the SAME string (shared social_instance_not_
    # found_msg) but under the family's `error` key; HttpStore restores the
    # tool's `message` key -> byte-identical to DirectStore. This is the whole
    # point of aligning the route wording onto the shared source.
    route_body = {"success": False, "error": social_instance_not_found_msg(AGENT)}
    h = _social_http(monkeypatch, route_body=route_body)
    hr = asyncio.run(h.delete_entity(AGENT, "user_x"))
    assert hr == expected == dr


def test_social_merge_method_failure_parity(monkeypatch):
    fail = {"success": False, "message": "Source entity not found: s1"}
    d = _social_direct(monkeypatch, method_result=fail)
    dr = asyncio.run(d.merge_entities(AGENT, "s1", "t1", True))
    assert dr == fail
    # Route normalized the method's `message` failure to `error`; HttpStore's
    # _social_write_message is the exact inverse.
    h = _social_http(monkeypatch, route_body={"success": False, "error": "Source entity not found: s1"})
    hr = asyncio.run(h.merge_entities(AGENT, "s1", "t1", True))
    assert hr == fail == dr


def test_social_http_unreachable_is_message_keyed(monkeypatch):
    # Transport degradation must use the social tool's `message` failure key,
    # not `_parse_dict`'s default `error`, so the agent sees a uniform shape.
    def boom(request):
        raise httpx.ConnectError("no route")

    _patch_http(monkeypatch, boom)
    store = HttpStore("http://backend:8000")
    out = asyncio.run(store.extract_entity_info(AGENT, "user_x", {}, "merge"))
    assert out["success"] is False
    assert "message" in out and "error" not in out
    assert "unreachable" in out["message"]


def test_social_direct_forwards_args_to_the_right_method(monkeypatch):
    # FakeSocial records (method_name, kwargs); assert DirectStore forwards each
    # tool's params to the CORRECT method with the right keys — a swap
    # (source/target), a dropped update_mode/keep_target_name, or extract calling
    # merge would all be caught here.
    d = _social_direct(monkeypatch, method_result={"success": True, "message": "ok"})
    asyncio.run(d.merge_entities(AGENT, "src1", "tgt1", False))
    assert d._calls["method"] == (
        "merge",
        {"source_entity_id": "src1", "target_entity_id": "tgt1",
         "instance_id": "social_1", "keep_target_name": False},
    )

    d2 = _social_direct(monkeypatch, method_result={"success": True, "message": "ok"})
    asyncio.run(d2.extract_entity_info(AGENT, "ent9", {"entity_name": "Z"}, "replace"))
    assert d2._calls["method"] == (
        "extract",
        {"entity_id": "ent9", "instance_id": "social_1",
         "updates": {"entity_name": "Z"}, "update_mode": "replace"},
    )


def test_social_id_bounds_rejected_identically_on_both(monkeypatch):
    # The route enforces entity-id Field(min_length=1, max_length=128) as a 422;
    # both stores must mirror it so a local caller can't extract an empty-id
    # entity the cloud caller would 422 on (store.py parity invariant).
    d = _social_direct(monkeypatch, method_result={"success": True, "message": "x"})
    h = _social_http(monkeypatch, route_body={"success": True, "message": "x"})
    empty = {"success": False, "message": "entity id is empty"}
    assert asyncio.run(d.extract_entity_info(AGENT, "", {}, "merge")) == empty
    assert asyncio.run(h.extract_entity_info(AGENT, "", {}, "merge")) == empty  # rejected pre-send
    # DirectStore short-circuited before touching the module method.
    assert d._calls == {}

    long_id = "a" * 129
    long_err = {"success": False, "message": "entity id too long (max 128 chars)"}
    assert asyncio.run(d.merge_entities(AGENT, long_id, "t", True)) == long_err
    assert asyncio.run(h.merge_entities(AGENT, long_id, "t", True)) == long_err


def test_social_http_forwards_to_the_right_route_and_body(monkeypatch):
    # The cloud-only side: assert HttpStore POSTs to the correct endpoint with
    # the correct body. Catches a wrong path (/delete_entity vs the real
    # /delete-entity), a source/target swap, or a dropped keep_target_name/
    # update_mode — none of which the DirectStore forwarding test can reach.
    # One handler records every request (patching httpx per-call would chain the
    # MockTransport subclasses and the wrong one would win).
    import json as _json

    seen: list = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = _json.loads(request.content) if request.content else None
        seen.append((request.url.path, body))
        return httpx.Response(200, json={"success": True, "message": "ok"})

    _patch_http(monkeypatch, handler)
    h = HttpStore("http://backend:8000")

    asyncio.run(h.merge_entities(AGENT, "src1", "tgt1", False))
    asyncio.run(h.delete_entity(AGENT, "ent9"))
    asyncio.run(h.extract_entity_info(AGENT, "ent9", {"entity_name": "Z"}, "replace"))

    assert seen[0][0].endswith("/social-network/merge")
    assert seen[0][1] == {
        "source_entity_id": "src1", "target_entity_id": "tgt1", "keep_target_name": False,
    }
    assert seen[1][0].endswith("/social-network/delete-entity")
    assert seen[1][1] == {"entity_id": "ent9"}
    assert seen[2][0].endswith("/social-network/extract")
    assert seen[2][1] == {
        "entity_id": "ent9", "updates": {"entity_name": "Z"}, "update_mode": "replace",
    }


def test_social_direct_db_failure_stays_in_band(monkeypatch):
    # DirectStore invariant (module docstring): only ever return a dict. A local
    # db failure during instance resolution must degrade to the tool's message
    # shape, NOT escape as an exception — else the same fault is a message dict
    # on HttpStore ("backend unreachable") but a raised error on Direct.
    class BoomRepo:
        def __init__(self, db):
            pass

        async def get_by_agent(self, agent_id, module_class):
            raise RuntimeError("db locked")

    monkeypatch.setattr("xyz_agent_context.repository.InstanceRepository", BoomRepo)
    store = DirectStore()

    async def fake_db():
        return object()

    store._db = fake_db  # type: ignore[method-assign]
    # Both a write and a read must degrade in-band (reads share _social_module).
    out = asyncio.run(store.delete_entity(AGENT, "ent9"))
    assert out["success"] is False
    assert "message" in out and "error" not in out
    assert "db locked" in out["message"]

    read = asyncio.run(store.search_social_network(AGENT, "alice", "auto", 5))
    assert read["success"] is False
    assert "message" in read and "error" not in read
    assert "db locked" in read["message"]
    assert read["results"] == []


# ---------------------------------------------------------------------------
# social reads (PR-5): search / get_contact_info / get_agent_social_stats
# ---------------------------------------------------------------------------


def test_social_search_parity(monkeypatch):
    body = {"success": True, "search_type": "keyword", "results": [{"entity_id": "u1"}], "count": 1}
    d = _social_direct(monkeypatch, search_result=body)
    dr = asyncio.run(d.search_social_network(AGENT, "alice", "auto", 5))
    assert dr == body  # search_network's dict passes through raw
    h = _social_http(monkeypatch, route_body=body)
    hr = asyncio.run(h.search_social_network(AGENT, "alice", "auto", 5))
    assert hr == body == dr


def test_social_contact_parity(monkeypatch):
    # Both DirectStore and the route shape recall_entity_info via the SAME
    # format_contact_result — feed the http side the real function's output so
    # the equality is a genuine cross-path check.
    from xyz_agent_context.module.social_network_module import format_contact_result

    recall = {"success": True, "entity": {"entity_name": "Alice", "contact_info": {"email": "a@x.com"}}}
    expected = {"success": True, "entity_id": "u1", "entity_name": "Alice", "contact_info": {"email": "a@x.com"}}
    d = _social_direct(monkeypatch, recall_result=recall)
    dr = asyncio.run(d.get_contact_info(AGENT, "u1"))
    assert dr == expected
    h = _social_http(monkeypatch, route_body=format_contact_result("u1", recall))
    hr = asyncio.run(h.get_contact_info(AGENT, "u1"))
    assert hr == expected == dr


def test_social_contact_not_found_parity(monkeypatch):
    from xyz_agent_context.module.social_network_module import format_contact_result

    recall = {"success": False, "message": "No information found for entity: u9"}
    expected = {"success": False, "message": "No information found for entity: u9"}
    d = _social_direct(monkeypatch, recall_result=recall)
    dr = asyncio.run(d.get_contact_info(AGENT, "u9"))
    assert dr == expected
    h = _social_http(monkeypatch, route_body=format_contact_result("u9", recall))
    hr = asyncio.run(h.get_contact_info(AGENT, "u9"))
    assert hr == expected == dr


def test_social_stats_parity(monkeypatch):
    from xyz_agent_context.module.social_network_module import format_stats_result

    stats_list = [{"entity_name": "Bob", "interaction_count": 3}]
    expected = {"success": True, "sort_by": "recent", "count": 1, "results": stats_list}
    d = _social_direct(monkeypatch, stats_result=stats_list)
    dr = asyncio.run(d.get_agent_social_stats(AGENT, "recent", 5, None))
    assert dr == expected
    h = _social_http(monkeypatch, route_body=format_stats_result("recent", stats_list))
    hr = asyncio.run(h.get_agent_social_stats(AGENT, "recent", 5, None))
    assert hr == expected == dr


def test_social_read_no_instance_parity(monkeypatch):
    from xyz_agent_context.module.social_network_module import social_instance_not_found_msg

    # search/stats no-instance carry results:[]; contact does NOT (matches tools).
    search_exp = {"success": False, "message": social_instance_not_found_msg(AGENT), "results": []}
    d = _social_direct(monkeypatch, has_instance=False)
    assert asyncio.run(d.search_social_network(AGENT, "x", "auto", 5)) == search_exp
    h = _social_http(monkeypatch, route_body=dict(search_exp))
    assert asyncio.run(h.search_social_network(AGENT, "x", "auto", 5)) == search_exp

    contact_exp = {"success": False, "message": social_instance_not_found_msg(AGENT)}
    d2 = _social_direct(monkeypatch, has_instance=False)
    assert asyncio.run(d2.get_contact_info(AGENT, "u1")) == contact_exp


def test_social_read_bounds_parity(monkeypatch):
    empty = {"success": False, "message": "search_keyword is empty", "results": []}
    d = _social_direct(monkeypatch, search_result={"success": True, "results": []})
    h = _social_http(monkeypatch, route_body={"success": True, "results": []})
    assert asyncio.run(d.search_social_network(AGENT, "", "auto", 5)) == empty
    assert asyncio.run(h.search_social_network(AGENT, "", "auto", 5)) == empty  # rejected pre-send
    # top_k=200 clamps to 100 on both; DirectStore records the kwargs it passed.
    asyncio.run(d.search_social_network(AGENT, "alice", "auto", 200))
    assert d._calls["method"] == (
        "search",
        {"search_keyword": "alice", "instance_id": "social_1", "search_type": "auto", "top_k": 100},
    )


def test_social_http_read_forwards_to_the_right_route_and_body(monkeypatch):
    import json as _json

    seen: list = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = _json.loads(request.content) if request.content else None
        seen.append((request.url.path, body))
        return httpx.Response(200, json={"success": True, "results": []})

    _patch_http(monkeypatch, handler)
    h = HttpStore("http://backend:8000")

    asyncio.run(h.search_social_network(AGENT, "alice", "auto", 5))
    asyncio.run(h.get_contact_info(AGENT, "u1"))
    asyncio.run(h.get_agent_social_stats(AGENT, "frequent", 10, ["expert:fe"]))

    assert seen[0][0].endswith("/social-network/recall")
    assert seen[0][1] == {"search_keyword": "alice", "search_type": "auto", "top_k": 5}
    assert seen[1][0].endswith("/social-network/contact")
    assert seen[1][1] == {"entity_id": "u1"}
    assert seen[2][0].endswith("/social-network/stats")
    assert seen[2][1] == {"sort_by": "frequent", "top_k": 10, "filter_tags": ["expert:fe"]}


# ---------------------------------------------------------------------------
# social create_agent (PR-6): provisioning through the seam
# ---------------------------------------------------------------------------


def _create_agent_direct(monkeypatch, *, caller_created_by="u1", caller_exists=True, warnings=None):
    """DirectStore with AgentRepository + provision_new_agent stubbed."""
    store = DirectStore()

    async def fake_db():
        return object()

    store._db = fake_db  # type: ignore[method-assign]

    class FakeAgent:
        created_by = caller_created_by
        agent_name = "Creator"

    class FakeAgentRepo:
        def __init__(self, db):
            pass

        async def get_agent(self, agent_id):
            return FakeAgent() if caller_exists else None

    class FakeProvisionResult:
        def __init__(self):
            self.warnings = warnings or []

    async def fake_provision(db, *, agent_id, user_id, agent_name, agent_description, awareness):
        return FakeProvisionResult()

    monkeypatch.setattr("xyz_agent_context.repository.AgentRepository", FakeAgentRepo)
    monkeypatch.setattr("xyz_agent_context.bootstrap.provision.provision_new_agent", fake_provision)
    return store


def test_create_agent_success_parity(monkeypatch):
    from xyz_agent_context.module.social_network_module import format_create_agent_success

    expected = format_create_agent_success("Scout", "agent_new123", [])
    d = _create_agent_direct(monkeypatch)
    dr = asyncio.run(d.create_agent("agent_creator", "agent_new123", "Scout", "I am Scout", ""))
    assert dr == expected
    # The route builds the SAME dict via the shared format_create_agent_success
    # for the same (agent_name, new_agent_id) — parity holds because the id is an
    # input, not independently minted per path.
    h = _social_http(monkeypatch, route_body=format_create_agent_success("Scout", "agent_new123", []))
    hr = asyncio.run(h.create_agent("agent_creator", "agent_new123", "Scout", "I am Scout", ""))
    assert hr == expected == dr


def test_create_agent_warnings_surfaced_on_both(monkeypatch):
    from xyz_agent_context.module.social_network_module import format_create_agent_success

    expected = format_create_agent_success("Scout", "agent_n", ["instance_factory: boom"])
    assert expected["warnings"] == ["instance_factory: boom"]
    d = _create_agent_direct(monkeypatch, warnings=["instance_factory: boom"])
    dr = asyncio.run(d.create_agent("agent_creator", "agent_n", "Scout", "aw", ""))
    assert dr == expected
    h = _social_http(monkeypatch, route_body=expected)
    assert asyncio.run(h.create_agent("agent_creator", "agent_n", "Scout", "aw", "")) == expected


def test_create_agent_no_owner_parity(monkeypatch):
    from xyz_agent_context.module.social_network_module import CREATE_AGENT_NO_OWNER_MSG

    expected = {"success": False, "message": CREATE_AGENT_NO_OWNER_MSG}
    d = _create_agent_direct(monkeypatch, caller_created_by=None)
    assert asyncio.run(d.create_agent("agent_creator", "agent_n", "Scout", "aw", "")) == expected
    # A caller that doesn't exist at all takes the same branch.
    d2 = _create_agent_direct(monkeypatch, caller_exists=False)
    assert asyncio.run(d2.create_agent("agent_creator", "agent_n", "Scout", "aw", "")) == expected
    # Route returns the same string under `error`; HttpStore restores `message`.
    h = _social_http(monkeypatch, route_body={"success": False, "error": CREATE_AGENT_NO_OWNER_MSG})
    assert asyncio.run(h.create_agent("agent_creator", "agent_n", "Scout", "aw", "")) == expected


def test_create_agent_http_forwards_the_minted_id_and_fields(monkeypatch):
    import json as _json

    seen: list = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append((request.url.path, _json.loads(request.content)))
        return httpx.Response(200, json={"success": True, "new_agent_id": "x", "agent_name": "Scout", "message": "ok"})

    _patch_http(monkeypatch, handler)
    h = HttpStore("http://backend:8000")
    asyncio.run(h.create_agent("agent_creator", "agent_new123", "Scout", "I am Scout", "desc"))
    assert seen[0][0].endswith("/social-network/create-agent")
    assert seen[0][1] == {
        "new_agent_id": "agent_new123", "agent_name": "Scout",
        "awareness": "I am Scout", "agent_description": "desc",
    }


# ---------------------------------------------------------------------------
# basic_info narrative/event (PR-7): view_narrative / view_event / switch
# ---------------------------------------------------------------------------


class _FakeNarrDb:
    """Minimal AsyncDatabaseClient stand-in for the _narrative_reads helpers."""

    def __init__(self, *, narratives=None, events=None, links=None, memories=None):
        self._narratives = narratives or {}   # narrative_id -> row
        self._events = events or {}            # event_id -> row
        self._links = links or []              # instance_narrative_links rows
        self._memories = memories or {}        # instance_id -> memory row

    async def get_one(self, table, filters):
        if table == "narratives":
            return self._narratives.get(filters["narrative_id"])
        if table == "events":
            row = self._events.get(filters["event_id"])
            if row and row.get("agent_id") != filters.get("agent_id"):
                return None
            return row
        raise AssertionError(table)

    async def get(self, table, filters, limit=None, order_by=None):
        assert table == "instance_narrative_links"
        return [r for r in self._links if r["narrative_id"] == filters["narrative_id"]]

    async def get_by_ids(self, table, col, ids):
        assert table == "instance_json_format_memory_chat"
        # Real contract: order-preserving, MISSING ids padded with None.
        return [self._memories.get(i) for i in ids]


def _basic_direct(monkeypatch, db):
    store = DirectStore()

    async def fake_db():
        return db

    store._db = fake_db  # type: ignore[method-assign]
    return store


def test_view_narrative_parity(monkeypatch):
    from xyz_agent_context.module.basic_info_module._narrative_reads import fetch_narrative_view

    db = _FakeNarrDb(
        narratives={"nar_1": {"agent_id": AGENT, "narrative_info": {"name": "Trip", "description": "d", "current_summary": "s"}, "topic_keywords": ["k"]}},
        links=[{"narrative_id": "nar_1", "instance_id": "chat_a", "created_at": "2026-01-01"}],
        memories={"chat_a": {"instance_id": "chat_a", "memory": {"messages": [{"role": "user", "content": "hi", "meta_data": {"timestamp": "2026-01-01T00:00:00", "event_id": "evt_1"}}]}}},
    )
    expected = asyncio.run(fetch_narrative_view(db, AGENT, "nar_1"))
    assert expected["success"] is True and expected["name"] == "Trip" and expected["message_count"] == 1

    d = _basic_direct(monkeypatch, db)
    dr = asyncio.run(d.view_narrative(AGENT, "nar_1"))
    assert dr == expected
    # the route returns the SAME fetch_narrative_view dict → http passes it through
    h = _social_http(monkeypatch, route_body=expected)
    assert asyncio.run(h.view_narrative(AGENT, "nar_1")) == expected == dr


def test_view_narrative_cross_tenant_is_not_found_on_both(monkeypatch):
    # The old raw-SQL tool returned ANY agent's narrative by id; the seam scopes
    # to the caller. A narrative owned by someone else reads as not-found.
    db = _FakeNarrDb(narratives={"nar_1": {"agent_id": "other_agent", "narrative_info": {}, "topic_keywords": []}})
    expected = {"success": False, "error": "narrative nar_1 not found"}
    d = _basic_direct(monkeypatch, db)
    assert asyncio.run(d.view_narrative(AGENT, "nar_1")) == expected
    h = _social_http(monkeypatch, route_body=expected)
    assert asyncio.run(h.view_narrative(AGENT, "nar_1")) == expected


def test_view_event_parity_and_agent_scoped(monkeypatch):
    from xyz_agent_context.module.basic_info_module._narrative_reads import fetch_event_view

    db = _FakeNarrDb(events={"evt_1": {"agent_id": AGENT, "narrative_id": "nar_1", "trigger": "manual", "trigger_source": "user", "env_context": {"input": "go"}, "final_output": "done", "event_log": "log", "created_at": "2026-01-01T00:00:00"}})
    expected = asyncio.run(fetch_event_view(db, AGENT, "evt_1"))
    assert expected == {"success": True, "event_id": "evt_1", "narrative_id": "nar_1", "trigger": "manual", "trigger_source": "user", "time": "2026-01-01T00:00:00", "input": "go", "final_output": "done", "event_log": "log"}
    d = _basic_direct(monkeypatch, db)
    assert asyncio.run(d.view_event(AGENT, "evt_1")) == expected
    h = _social_http(monkeypatch, route_body=expected)
    assert asyncio.run(h.view_event(AGENT, "evt_1")) == expected
    # wrong agent → not found (get_one filters agent_id)
    assert asyncio.run(d.view_event("other", "evt_1")) == {"success": False, "error": "event evt_1 not found"}


def test_switch_narrative_parity(monkeypatch):
    db = _FakeNarrDb(narratives={"nar_1": {"agent_id": AGENT}})
    ok = {"success": True, "narrative_id": "nar_1", "message": "This turn will be attributed to this narrative."}
    d = _basic_direct(monkeypatch, db)
    assert asyncio.run(d.switch_narrative(AGENT, "nar_1")) == ok
    h = _social_http(monkeypatch, route_body=ok)
    assert asyncio.run(h.switch_narrative(AGENT, "nar_1")) == ok
    # cross-tenant / missing → not found on both
    nf = {"success": False, "error": "narrative nar_x not found"}
    assert asyncio.run(d.switch_narrative(AGENT, "nar_x")) == nf


def test_basic_info_http_forwards_to_the_right_routes(monkeypatch):
    import json as _json

    seen: list = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = _json.loads(request.content) if request.content else None
        seen.append((request.method, request.url.path, body))
        return httpx.Response(200, json={"success": True})

    _patch_http(monkeypatch, handler)
    h = HttpStore("http://backend:8000")
    asyncio.run(h.view_narrative(AGENT, "nar_1"))
    asyncio.run(h.view_event(AGENT, "evt_1"))
    asyncio.run(h.switch_narrative(AGENT, "nar_1"))
    assert seen[0] == ("GET", f"/api/agents/{AGENT}/narratives/nar_1", None)
    assert seen[1] == ("GET", f"/api/agents/{AGENT}/events/evt_1", None)
    assert seen[2] == ("POST", f"/api/agents/{AGENT}/narratives/nar_1/switch", {})


def test_view_narrative_skips_memoryless_chat_instance(monkeypatch):
    # A chat_ instance can be LINKED (step_1) before its memory row is written
    # (step_5) — get_by_ids pads the missing row with None. The read must skip
    # it, not crash. Regression guard for the get_by_ids-None-contract bug.
    db = _FakeNarrDb(
        narratives={"nar_1": {"agent_id": AGENT, "narrative_info": {"name": "T"}, "topic_keywords": []}},
        links=[
            {"narrative_id": "nar_1", "instance_id": "chat_has_mem", "created_at": "1"},
            {"narrative_id": "nar_1", "instance_id": "chat_no_mem", "created_at": "2"},  # never got a memory row
        ],
        memories={"chat_has_mem": {"instance_id": "chat_has_mem", "memory": {"messages": [
            {"role": "user", "content": "hi", "meta_data": {"timestamp": "2026-01-01T00:00:00", "event_id": "e"}},
        ]}}},
    )
    d = _basic_direct(monkeypatch, db)
    out = asyncio.run(d.view_narrative(AGENT, "nar_1"))
    assert out["success"] is True
    assert out["message_count"] == 1  # chat_no_mem skipped, not a NoneType crash


# ---------------------------------------------------------------------------
# job reads (PR-8): job_retrieval_by_id / _semantic / _by_keywords
# ---------------------------------------------------------------------------


class _V:  # tiny .value holder for job_type / status
    def __init__(self, v):
        self.value = v


class _FakeJob:
    def __init__(self, job_id="job_1", agent_id=AGENT, description="D"):
        self.job_id = job_id
        self.agent_id = agent_id
        self.user_id = "u1"
        self.instance_id = "job_inst"
        self.title = "T"
        self.description = description
        self.payload = {}
        self.job_type = _V("one_off")
        self.trigger_config = None
        self.status = _V("active")
        self.notification_method = "none"
        self.next_run_at_local = None
        self.next_run_tz = "UTC"
        self.last_run_at_local = None
        self.last_run_tz = "UTC"
        self.related_entity_id = None
        self.narrative_id = None
        self.iteration_count = 0
        self.process = "proc"
        self.last_error = None
        self.created_at = None
        self.updated_at = None


def _patch_job_repo(monkeypatch, *, job=None, search_hits=None, keyword_hits=None):
    class _FakeJobRepo:
        def __init__(self, db):
            pass

        async def get_job(self, job_id):
            return job if (job and job.job_id == job_id) else None

        async def search_keyword(self, agent_id, query, user_id, status, limit):
            return search_hits or []

        async def search_by_keywords(self, agent_id, keywords, user_id, status, limit):
            return keyword_hits or []

    monkeypatch.setattr("xyz_agent_context.module.job_module._job_reads.JobRepository", _FakeJobRepo)


def test_job_by_id_parity(monkeypatch):
    from xyz_agent_context.module.job_module import fetch_job_by_id

    job = _FakeJob()
    _patch_job_repo(monkeypatch, job=job)
    db = object()
    expected = asyncio.run(fetch_job_by_id(db, AGENT, "job_1"))
    assert expected["success"] is True and expected["job"]["job_id"] == "job_1"
    assert expected["job"]["process"] == "proc"  # by_id-only extra field

    d = _basic_direct(monkeypatch, db)
    dr = asyncio.run(d.job_retrieval_by_id(AGENT, "job_1"))
    assert dr == expected
    h = _social_http(monkeypatch, route_body=expected)
    assert asyncio.run(h.job_retrieval_by_id(AGENT, "job_1")) == expected == dr


def test_job_by_id_cross_agent_is_access_denied(monkeypatch):
    job = _FakeJob(agent_id="other_agent")
    _patch_job_repo(monkeypatch, job=job)
    d = _basic_direct(monkeypatch, object())
    out = asyncio.run(d.job_retrieval_by_id(AGENT, "job_1"))
    assert out == {"success": False, "error": "Access denied: Job belongs to a different agent"}


def test_job_by_id_not_found(monkeypatch):
    _patch_job_repo(monkeypatch, job=None)
    d = _basic_direct(monkeypatch, object())
    assert asyncio.run(d.job_retrieval_by_id(AGENT, "job_x")) == {"success": False, "error": "Job not found: job_x"}


def test_job_semantic_parity_and_limit_clamp(monkeypatch):
    from xyz_agent_context.module.job_module import search_jobs_semantic

    job = _FakeJob()
    _patch_job_repo(monkeypatch, search_hits=[(job, 0.9)])
    db = object()
    expected = asyncio.run(search_jobs_semantic(db, AGENT, "news", None, None, 100))
    assert expected["success"] is True
    assert expected["jobs"][0]["similarity_score"] == 0.9

    d = _basic_direct(monkeypatch, db)
    # limit=500 must clamp to 100 (route le=100) on both paths
    dr = asyncio.run(d.job_retrieval_semantic(AGENT, "news", None, None, 500))
    assert dr == expected
    h = _social_http(monkeypatch, route_body=expected)
    assert asyncio.run(h.job_retrieval_semantic(AGENT, "news", None, None, 500)) == expected == dr


def test_job_semantic_invalid_status_parity(monkeypatch):
    _patch_job_repo(monkeypatch, search_hits=[])
    expected = {"success": False, "error": "Invalid status: bogus. Valid values: pending, active, running, completed, failed"}
    d = _basic_direct(monkeypatch, object())
    assert asyncio.run(d.job_retrieval_semantic(AGENT, "q", None, "bogus", 10)) == expected
    h = _social_http(monkeypatch, route_body=expected)
    assert asyncio.run(h.job_retrieval_semantic(AGENT, "q", None, "bogus", 10)) == expected


def test_job_by_keywords_parity_and_truncation(monkeypatch):
    from xyz_agent_context.module.job_module import search_jobs_by_keywords

    long_desc = "x" * 250
    job = _FakeJob(description=long_desc)
    _patch_job_repo(monkeypatch, keyword_hits=[job])
    db = object()
    expected = asyncio.run(search_jobs_by_keywords(db, AGENT, ["news"], None, None, 20))
    assert expected["jobs"][0]["description"].endswith("...")  # >200 truncated
    assert len(expected["jobs"][0]["description"]) == 203

    d = _basic_direct(monkeypatch, db)
    dr = asyncio.run(d.job_retrieval_by_keywords(AGENT, ["news"], None, None, 20))
    assert dr == expected
    h = _social_http(monkeypatch, route_body=expected)
    assert asyncio.run(h.job_retrieval_by_keywords(AGENT, ["news"], None, None, 20)) == expected == dr


def test_job_http_forwards_to_the_right_routes(monkeypatch):
    import json as _json

    seen: list = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = _json.loads(request.content) if request.content else None
        seen.append((request.method, request.url.path, body))
        return httpx.Response(200, json={"success": True})

    _patch_http(monkeypatch, handler)
    h = HttpStore("http://backend:8000")
    asyncio.run(h.job_retrieval_by_id(AGENT, "job_1"))
    asyncio.run(h.job_retrieval_semantic(AGENT, "news", "u2", "active", 5))
    asyncio.run(h.job_retrieval_by_keywords(AGENT, ["a", "b"], None, None, 7))
    assert seen[0] == ("GET", f"/api/agents/{AGENT}/jobs/job_1", None)
    assert seen[1] == ("POST", f"/api/agents/{AGENT}/jobs/search-semantic",
                       {"query": "news", "user_id": "u2", "status": "active", "limit": 5})
    assert seen[2] == ("POST", f"/api/agents/{AGENT}/jobs/search-keywords",
                       {"keywords": ["a", "b"], "user_id": None, "status": None, "limit": 7})


def test_job_search_input_bounds_rejected_identically_on_both(monkeypatch):
    # Empty/over-long query and empty keywords must be rejected the SAME on both
    # stores — the route enforces them as 422, so DirectStore must pre-reject too
    # (else a local search succeeds on an input the cloud path 422s on).
    _patch_job_repo(monkeypatch, search_hits=[], keyword_hits=[])
    d = _basic_direct(monkeypatch, object())
    h = _social_http(monkeypatch, route_body={"success": True})

    empty_q = {"success": False, "error": "query is empty"}
    assert asyncio.run(d.job_retrieval_semantic(AGENT, "", None, None, 10)) == empty_q
    assert asyncio.run(h.job_retrieval_semantic(AGENT, "", None, None, 10)) == empty_q  # rejected pre-send

    long_q = {"success": False, "error": "query too long (max 512 chars)"}
    assert asyncio.run(d.job_retrieval_semantic(AGENT, "a" * 513, None, None, 10)) == long_q
    assert asyncio.run(h.job_retrieval_semantic(AGENT, "a" * 513, None, None, 10)) == long_q

    empty_kw = {"success": False, "error": "keywords is empty"}
    assert asyncio.run(d.job_retrieval_by_keywords(AGENT, [], None, None, 20)) == empty_kw
    assert asyncio.run(h.job_retrieval_by_keywords(AGENT, [], None, None, 20)) == empty_kw
