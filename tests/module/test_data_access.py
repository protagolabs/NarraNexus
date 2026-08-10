"""
@file_name: test_data_access.py
@date: 2026-08-10
@description: AgentDataStore parity — DirectStore and HttpStore must produce the
SAME results (blueprint P0 is behaviour-preserving), and the factory picks the
transport by NARRANEXUS_BACKEND_URL.
"""
from __future__ import annotations

import pytest

import xyz_agent_context.module.data_access.factory as fac
import xyz_agent_context.module.data_access.store as st
import xyz_agent_context.repository as repo


class _FakeResp:
    def __init__(self, status):
        self.status_code = status

    def raise_for_status(self):
        return None

    def json(self):
        return {"awareness": "hi"}


class _FakeClient:
    def __init__(self, status):
        self._status = status

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def put(self, url, json):
        return _FakeResp(self._status)

    async def get(self, url):
        return _FakeResp(self._status)


def _stub_direct(monkeypatch, instance_id):
    async def _db(self):
        return object()

    async def _inst(self, db, agent_id):
        return instance_id

    monkeypatch.setattr(st.DirectStore, "_db", _db)
    monkeypatch.setattr(st.DirectStore, "_awareness_instance_id", _inst)


@pytest.mark.asyncio
async def test_direct_no_instance(monkeypatch):
    _stub_direct(monkeypatch, None)
    assert await st.DirectStore().update_awareness("a", "x") == st._no_instance_msg("a")


@pytest.mark.asyncio
async def test_direct_upsert(monkeypatch):
    _stub_direct(monkeypatch, "inst1")
    calls = []

    class FakeAwarenessRepo:
        def __init__(self, db):
            pass

        async def upsert(self, iid, aw):
            calls.append((iid, aw))

    monkeypatch.setattr(repo, "InstanceAwarenessRepository", FakeAwarenessRepo)
    assert await st.DirectStore().update_awareness("a", "profile") == st._AWARENESS_OK
    assert calls == [("inst1", "profile")]


@pytest.mark.asyncio
async def test_http_success_parity(monkeypatch):
    s = st.HttpStore("http://backend")

    async def _client(self):
        return _FakeClient(200)

    monkeypatch.setattr(st.HttpStore, "_client", _client)
    # HttpStore returns the SAME string DirectStore does.
    assert await s.update_awareness("a", "x") == st._AWARENESS_OK


@pytest.mark.asyncio
async def test_http_404_is_no_instance(monkeypatch):
    s = st.HttpStore("http://backend")

    async def _client(self):
        return _FakeClient(404)

    monkeypatch.setattr(st.HttpStore, "_client", _client)
    assert await s.update_awareness("a", "x") == st._no_instance_msg("a")


def test_factory_local_is_direct(monkeypatch):
    monkeypatch.delenv("NARRANEXUS_BACKEND_URL", raising=False)
    assert isinstance(fac.get_agent_data_store(), st.DirectStore)


def test_factory_cloud_is_http(monkeypatch):
    monkeypatch.setenv("NARRANEXUS_BACKEND_URL", "http://backend")
    assert isinstance(fac.get_agent_data_store(identity_headers={}), st.HttpStore)
