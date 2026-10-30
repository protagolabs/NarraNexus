"""
@file_name: test_chat_latest_mysql.py
@author: Bin Liang
@date: 2026-10-30
@description: MySQL twin for /chat-latest: the raw IN-list instance query (InstanceRepository.list_ids_by_agents_and_user) and the updated_at-keyed cache, on the dialect where updated_at comes back as a DATETIME(6) value instead of a string.

    docker run --rm -d -p 3306:3306 -e MYSQL_ROOT_PASSWORD=root -e MYSQL_DATABASE=nxtest --name nx-mysql-test mysql:8
    export NARRANEXUS_MYSQL_TEST_URL=mysql://root:root@127.0.0.1:3306/nxtest
"""
from __future__ import annotations

import json

import pytest
import pytest_asyncio

import narranexus_plugins.chat_module._chat_latest as cl
import narranexus_plugins.chat_module.routes as ch
from narranexus.platform.repository import InstanceRepository
from narranexus.platform.utils.db.database import AsyncDatabaseClient
from narranexus.platform.utils.db.db_backend_mysql import MySQLBackend
from narranexus.platform.utils.db.schema_registry import auto_migrate

from tests.mysql_dialect import mysql_configured, mysql_url, parse_mysql_url, skip_reason

pytestmark = pytest.mark.skipif(not mysql_configured(), reason=skip_reason("the /chat-latest IN-list query and updated_at cache"))

OWNER = "usr_mysql_chatlatest"
AGENTS = ("agent_mysql_cl_1", "agent_mysql_cl_2", "agent_mysql_cl_3")
INSTANCES = ("chat_mysql_cl_1", "chat_mysql_cl_2", "chat_mysql_cl_other")


def _memory(*rows):
    return json.dumps({"messages": [
        {"role": role, "content": "x", "meta_data": {"timestamp": ts}} for role, ts in rows
    ]})


async def _cleanup(client):
    for iid in INSTANCES:
        await client.delete("instance_json_format_memory_chat", {"instance_id": iid})
        await client.delete("module_instances", {"instance_id": iid})


@pytest_asyncio.fixture
async def mysql_client():
    backend = MySQLBackend(parse_mysql_url(mysql_url()))
    await backend.initialize()
    await auto_migrate(backend)
    client = await AsyncDatabaseClient.create_with_backend(backend)
    await _cleanup(client)
    yield client
    await _cleanup(client)
    await client.close()


async def _seed(client):
    rows = [
        ("chat_mysql_cl_1", AGENTS[0], OWNER),
        ("chat_mysql_cl_2", AGENTS[1], OWNER),
        ("chat_mysql_cl_other", AGENTS[0], "usr_someone_else"),
    ]
    for iid, aid, uid in rows:
        await client.insert("module_instances", {
            "instance_id": iid, "agent_id": aid, "user_id": uid,
            "module_class": "ChatModule", "status": "active",
        })
    await client.insert("instance_json_format_memory_chat",
                        {"instance_id": "chat_mysql_cl_1", "memory": _memory(("assistant", "2026-10-30T10:00:00")),
                         "updated_at": "2026-10-30 10:00:00.123456"})
    await client.insert("instance_json_format_memory_chat",
                        {"instance_id": "chat_mysql_cl_2", "memory": _memory(("assistant", "2026-10-30T11:00:00"))})
    await client.insert("instance_json_format_memory_chat",
                        {"instance_id": "chat_mysql_cl_other", "memory": _memory(("assistant", "2026-10-30T12:00:00"))})


@pytest.mark.asyncio
async def test_in_list_query_runs_on_mysql_and_scopes_to_the_user(mysql_client):
    await _seed(mysql_client)
    rows = await InstanceRepository(mysql_client).list_ids_by_agents_and_user(list(AGENTS), OWNER, "ChatModule")
    assert sorted(r["instance_id"] for r in rows) == ["chat_mysql_cl_1", "chat_mysql_cl_2"]
    assert {r["agent_id"] for r in rows} == {AGENTS[0], AGENTS[1]}


@pytest.mark.asyncio
async def test_endpoint_and_cache_on_mysql(mysql_client, monkeypatch):
    # Called in-loop rather than through TestClient: the aiomysql pool is bound
    # to this test's event loop, and TestClient runs the app on another one.
    from starlette.requests import Request
    from backend.plugin_sdk_host import install_web_host

    await _seed(mysql_client)

    async def _db():
        return mysql_client

    monkeypatch.setattr(ch, "get_db_client", _db)
    cl._LATEST_CACHE.clear()
    install_web_host()

    async def latest():
        request = Request({"type": "http", "method": "GET", "path": "/api/agents/chat-latest", "headers": []})
        request.state.user_id = OWNER
        res = await ch.get_chat_latest(request, agent_ids=",".join(AGENTS))
        assert res.success, res.error
        return res.latest

    try:
        assert await latest() == {AGENTS[0]: "2026-10-30T10:00:00", AGENTS[1]: "2026-10-30T11:00:00"}
        # updated_at is a DATETIME(6) value here: an unchanged row must compare equal
        # (served from the cache) and a rewritten row must not.
        parsed = []
        real = cl._parse_row
        monkeypatch.setattr(cl, "_parse_row", lambda *a: parsed.append(1) or real(*a))
        assert (await latest())[AGENTS[0]] == "2026-10-30T10:00:00"
        assert parsed == []
        # Through the production writer, whose upsert bumps updated_at
        # (CURRENT_TIMESTAMP(6)); a plain UPDATE would leave it unchanged.
        from narranexus.platform.repository.event_memory_repository import EventMemoryRepository

        repo = EventMemoryRepository(AGENTS[0], OWNER, mysql_client)
        assert await repo.add_instance_json_format_memory(
            "ChatModule", "chat_mysql_cl_1",
            json.loads(_memory(("assistant", "2026-10-30T10:00:00"), ("user", "2026-10-30T13:00:00"))),
        )
        assert (await latest())[AGENTS[0]] == "2026-10-30T13:00:00"
        assert parsed == [1]
    finally:
        cl._LATEST_CACHE.clear()
