"""
@file_name: test_unread_cursor_mysql.py
@author:
@date: 2026-08-11
@description: Real-MySQL coverage for the unread cursor's hand-written SQL.

Three new raw statements land in `local_bus.py` with this change: `ack_read`'s
guarded UPDATE, `count_unread`'s COUNT, and `get_unread`'s two-parameter WHERE.
The SQLite suite structurally cannot see a dialect error in any of them —
`LocalMessageBus._db` is the RAW backend, so what SQLite runs and what MySQL
runs are not the same text.

Project policy for new raw SQL, same as `test_cascade_stop_mysql.py` and
`test_team_work_repository_mysql.py`.

Enable with a throwaway MySQL DSN:

    export NARRANEXUS_MYSQL_TEST_URL=\\
        "mysql://root:xyz_root_pass@127.0.0.1:3306/xyz_agent_context"
"""
from __future__ import annotations

import os

import pytest
import pytest_asyncio

from xyz_agent_context.message_bus.local_bus import LocalMessageBus
from xyz_agent_context.utils.db.db_backend_mysql import MySQLBackend
from xyz_agent_context.utils.db.schema_registry import auto_migrate

MYSQL_URL_ENV = "NARRANEXUS_MYSQL_TEST_URL"
_PREFIX = "mysqlunread"
ME = f"{_PREFIX}_me"
PEER = f"{_PREFIX}_peer"
ROOM = f"{_PREFIX}_room"


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
        f"{MYSQL_URL_ENV} not set. Validates the unread cursor's raw SQL against "
        f"a real MySQL dialect (guarded UPDATE + COUNT + two-parameter WHERE). "
        f"Example DSN: mysql://root:xyz_root_pass@127.0.0.1:3306/xyz_agent_context"
    ),
)


@pytest_asyncio.fixture
async def bus():
    backend = MySQLBackend(_parse_mysql_url(os.environ[MYSQL_URL_ENV]))
    await backend.initialize()
    await auto_migrate(backend)
    b = LocalMessageBus(backend=backend)
    await backend.execute_write(
        "DELETE FROM bus_messages WHERE channel_id = %s", (ROOM,)
    )
    await backend.execute_write(
        "DELETE FROM bus_channel_members WHERE channel_id = %s", (ROOM,)
    )
    await backend.execute_write(
        "DELETE FROM bus_channels WHERE channel_id = %s", (ROOM,)
    )
    await backend.execute_write(
        "INSERT INTO bus_channels (channel_id, name, channel_type, created_by) "
        "VALUES (%s, %s, %s, %s)",
        (ROOM, "room", "group", "team_t1"),
    )
    for aid in (ME, PEER):
        await backend.execute_write(
            "INSERT INTO bus_channel_members (channel_id, agent_id) VALUES (%s, %s)",
            (ROOM, aid),
        )
    yield b
    try:
        for table, col in (
            ("bus_messages", "channel_id"),
            ("bus_channel_members", "channel_id"),
            ("bus_channels", "channel_id"),
        ):
            await backend.execute_write(
                f"DELETE FROM {table} WHERE {col} = %s", (ROOM,)
            )
    except Exception:  # noqa: BLE001 — teardown must not mask a failure
        pass
    await backend.close()


@pytest.mark.asyncio
async def test_get_unread_and_count_run_on_mysql(bus):
    """The two-parameter WHERE (`cm.agent_id` AND `m.from_agent`) plus COUNT."""
    for i in range(4):
        await bus.send_message(from_agent=PEER, to_channel=ROOM, content=f"m{i}")
    await bus.send_message(from_agent=ME, to_channel=ROOM, content="mine")

    assert await bus.count_unread(ME) == 4
    assert [m.content for m in await bus.get_unread(ME, limit=2)] == ["m2", "m3"]
    assert len(await bus.get_unread(ME)) == 4


@pytest.mark.asyncio
async def test_ack_read_runs_on_mysql_and_only_moves_forward(bus):
    """The guarded UPDATE — its WHERE carries the same value twice."""
    await bus.send_message(from_agent=PEER, to_channel=ROOM, content="first")
    rows = await bus._db.execute(
        "SELECT created_at FROM bus_messages WHERE channel_id = %s "
        "ORDER BY created_at DESC LIMIT 1",
        (ROOM,),
    )
    cutoff = rows[0]["created_at"]

    await bus.ack_read(ME, ROOM, cutoff)
    assert await bus.count_unread(ME) == 0

    # A stale timestamp must not drag the cursor back and resurface what was
    # already read.
    await bus.ack_read(ME, ROOM, "1971-01-01T00:00:00+00:00")
    assert await bus.count_unread(ME) == 0


@pytest.mark.asyncio
async def test_has_message_from_turn_runs_on_mysql(bus):
    """The three-column existence WHERE added for the team-room notice gate.

    A dialect error here would surface as an EXCEPTION inside the arm that
    decides whether to announce a delivery failure — i.e. exactly when the run
    is already degraded — so SQLite-only coverage is not enough.
    """
    await bus.send_message(
        from_agent=ME, to_channel=ROOM, content="mine", event_id="evt_x"
    )
    await bus.send_message(
        from_agent=PEER, to_channel=ROOM, content="theirs", event_id="evt_x"
    )

    assert await bus.has_message_from_turn(ROOM, ME, "evt_x") is True
    # Wrong turn, wrong sender, wrong channel: each column has to bite.
    assert await bus.has_message_from_turn(ROOM, ME, "evt_other") is False
    assert await bus.has_message_from_turn(ROOM, f"{_PREFIX}_ghost", "evt_x") is False
    assert await bus.has_message_from_turn("ch_nope", ME, "evt_x") is False
