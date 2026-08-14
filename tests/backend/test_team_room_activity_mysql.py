"""
@file_name: test_team_room_activity_mysql.py
@author: NarraNexus
@date: 2026-08-14
@description: The room-activity query against a real MySQL dialect.

`_team_room_activity` is the one piece of SQL in this change whose dialect
differences would show up in RESULTS rather than in an error: `created_at` is
TEXT compared lexicographically on SQLite and `DATETIME(6)` compared as a time
on MySQL, and the query leans on that comparison twice — once inside a `MAX()`
per channel, once joining that maximum back to the row that produced it.

Everything else about it (a derived table with an alias, `GROUP BY`, `NOT IN`,
`IS NULL`) is common to both, and `ONLY_FULL_GROUP_BY` is satisfied. But
"probably fine" is exactly the confidence level that produced the last two
dialect incidents, and a wrong answer here is silent: the sidebar shows the
wrong message, or no mark at all, and nothing errors.

Run with a throwaway MySQL:

    docker run --rm -d -p 3306:3306 -e MYSQL_ROOT_PASSWORD=root \\
        -e MYSQL_DATABASE=nxtest --name nx-mysql-test mysql:8
    export NARRANEXUS_MYSQL_TEST_URL=mysql://root:root@127.0.0.1:3306/nxtest
"""

from __future__ import annotations

import os

import pytest
import pytest_asyncio

from xyz_agent_context.message_bus.system_messages import PLATFORM_MSG_TYPES
from xyz_agent_context.schema.team_schema import TEAM_ROOM_OWNER_PREFIX
from xyz_agent_context.utils.db.database import AsyncDatabaseClient
from xyz_agent_context.utils.db.db_backend_mysql import MySQLBackend
from xyz_agent_context.utils.db.schema_registry import auto_migrate

from backend.routes.teams import _team_room_activity

MYSQL_URL_ENV = "NARRANEXUS_MYSQL_TEST_URL"


def _parse_mysql_url(url: str) -> dict:
    assert url.startswith("mysql://"), f"expected mysql://..., got {url!r}"
    body = url[len("mysql://") :]
    creds, _, host_db = body.partition("@")
    user, _, password = creds.partition(":")
    host_port, _, database = host_db.partition("/")
    host, _, port = host_port.partition(":")
    return {
        "host": host,
        "port": int(port) if port else 3306,
        "user": user,
        "password": password,
        "database": database,
    }


pytestmark = pytest.mark.skipif(
    not os.environ.get(MYSQL_URL_ENV),
    reason=(
        f"{MYSQL_URL_ENV} not set. These tests validate the room-activity "
        f"MAX + self-join against a real MySQL dialect, where `created_at` is a "
        f"DATETIME(6) rather than SQLite's lexicographic TEXT."
    ),
)

_PREFIX = "mysqlact"
TEAM = f"{_PREFIX}_team_1"
OTHER = f"{_PREFIX}_team_2"
USER = f"usr_{_PREFIX}"


@pytest_asyncio.fixture
async def mysql_client():
    backend = MySQLBackend(_parse_mysql_url(os.environ[MYSQL_URL_ENV]))
    await backend.initialize()
    await auto_migrate(backend)
    client = await AsyncDatabaseClient.create_with_backend(backend)
    yield client
    for table, col in (
        ("bus_messages", "channel_id"),
        ("bus_channels", "channel_id"),
        ("teams", "team_id"),
    ):
        try:
            await client.execute(
                f"DELETE FROM {table} WHERE {col} LIKE %s", (f"{_PREFIX}%",), fetch=False
            )
        except Exception:  # noqa: BLE001 — teardown must not mask a failure
            pass
    await client.close()


async def _room(db, team_id: str) -> str:
    channel_id = f"{_PREFIX}_ch_{team_id}"
    await db.insert(
        "bus_channels",
        {
            "channel_id": channel_id,
            "channel_type": "group",
            "created_by": f"{TEAM_ROOM_OWNER_PREFIX}{team_id}",
            "name": "Room",
        },
    )
    return channel_id


async def _say(db, channel_id: str, sender: str, content: str, at: str, msg_type="text"):
    await db.insert(
        "bus_messages",
        {
            "message_id": f"{_PREFIX}_{channel_id}_{at}",
            "channel_id": channel_id,
            "from_agent": sender,
            "content": content,
            "msg_type": msg_type,
            "created_at": at,
        },
    )


def _ts(i: int) -> str:
    return f"2026-08-14 12:{i // 60:02d}:{i % 60:02d}"


@pytest.mark.asyncio
async def test_the_newest_message_wins_under_a_datetime_column(mysql_client):
    """The `MAX()` and the join both compare a real DATETIME(6) here, not text."""
    channel = await _room(mysql_client, TEAM)
    await _say(mysql_client, channel, "agent_a", "older", _ts(1))
    await _say(mysql_client, channel, "agent_a", "newest", _ts(9))
    await _say(mysql_client, channel, "agent_a", "middle", _ts(5))

    got = await _team_room_activity(mysql_client, [TEAM], user_sender=USER)

    assert got[TEAM]["preview"] == "newest"


@pytest.mark.asyncio
async def test_the_exclusions_hold_in_this_dialect_too(mysql_client):
    """`from_agent != %s` and the `NOT IN` over platform types — the two rules
    that decide whether the mark is trustworthy at all."""
    channel = await _room(mysql_client, TEAM)
    await _say(mysql_client, channel, "agent_a", "the real answer", _ts(1))
    await _say(mysql_client, channel, USER, "thanks", _ts(2))
    await _say(mysql_client, channel, "agent_a", "swept", _ts(3), msg_type=PLATFORM_MSG_TYPES[0])

    got = await _team_room_activity(mysql_client, [TEAM], user_sender=USER)

    assert got[TEAM]["preview"] == "the real answer"


@pytest.mark.asyncio
async def test_rooms_stay_apart_when_several_are_queried_at_once(mysql_client):
    """The whole point of the single query: one statement, many rooms, and the
    `GROUP BY` has to keep them separate."""
    ours = await _room(mysql_client, TEAM)
    theirs = await _room(mysql_client, OTHER)
    await _say(mysql_client, ours, "agent_a", "ours", _ts(1))
    await _say(mysql_client, theirs, "agent_a", "theirs", _ts(2))

    got = await _team_room_activity(mysql_client, [TEAM, OTHER], user_sender=USER)

    assert got[TEAM]["preview"] == "ours"
    assert got[OTHER]["preview"] == "theirs"


@pytest.mark.asyncio
async def test_a_silent_room_reports_nothing(mysql_client):
    """An empty result set, not a row of NULLs — the caller distinguishes the
    two by presence, and a NULL row would render as a mark with no message."""
    await _room(mysql_client, TEAM)

    assert await _team_room_activity(mysql_client, [TEAM], user_sender=USER) == {}
