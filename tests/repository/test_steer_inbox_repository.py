"""
@file_name: test_steer_inbox_repository.py
@author: Bin Liang
@date: 2026-08-21
@description: SteerInboxRepository — the durable store for live-steering
injections destined for a running turn (keyed by an opaque run_id the
orchestrator assigns). Covers idempotent append (dedup by run_id+msg_id),
per-run unconsumed pull in arrival order, and consume-up-to.
"""

import pytest

from xyz_agent_context.repository.steer_inbox_repository import SteerInboxRepository
from xyz_agent_context.schema.steer_schema import SteerInjection
from xyz_agent_context.utils.db.dialect_errors import is_unique_violation


def _inj(run_id: str, msg_id: str, content: str = "x", source: str = "team") -> SteerInjection:
    return SteerInjection(
        run_id=run_id,
        msg_id=msg_id,
        role="user",
        content=content,
        sender_id="agent_sender",
        source=source,
    )


@pytest.mark.asyncio
async def test_append_then_pull_unconsumed_returns_it(db_client):
    repo = SteerInboxRepository(db_client)

    assert await repo.append(_inj("run1", "m1", content="hello")) is True

    pending = await repo.pull_unconsumed("run1")
    assert [p.content for p in pending] == ["hello"]
    assert pending[0].msg_id == "m1"
    assert pending[0].id is not None


@pytest.mark.asyncio
async def test_append_is_idempotent_per_run_and_msg(db_client):
    repo = SteerInboxRepository(db_client)

    assert await repo.append(_inj("run1", "m1")) is True
    # Same (run_id, msg_id) — a re-delivered message must not double-inject.
    assert await repo.append(_inj("run1", "m1", content="changed")) is False

    pending = await repo.pull_unconsumed("run1")
    assert len(pending) == 1  # no duplicate row


@pytest.mark.asyncio
async def test_unique_index_rejects_a_duplicate_direct_insert(db_client):
    # `append`'s pre-check short-circuits the ordinary duplicate, so this
    # guards the OTHER guarantee: the (run_id, msg_id) unique index itself,
    # which is what makes append's race branch correct. Inserting straight
    # past the repository (as a lost race would) must be rejected by the DB.
    # Drop the index from schema_registry and this goes green-to-red.
    row = {
        "run_id": "run1", "msg_id": "m1", "role": "user",
        "content": "x", "sender_id": "agent_sender", "source": "team",
    }
    await db_client.insert("steer_inbox", row)
    with pytest.raises(Exception) as exc_info:
        await db_client.insert("steer_inbox", dict(row))
    assert is_unique_violation(exc_info.value)


@pytest.mark.asyncio
async def test_pull_is_scoped_to_run_and_ordered_by_arrival(db_client):
    repo = SteerInboxRepository(db_client)
    await repo.append(_inj("run1", "m1", content="first"))
    await repo.append(_inj("run2", "other", content="other-run"))
    await repo.append(_inj("run1", "m2", content="second"))

    pending = await repo.pull_unconsumed("run1")
    assert [p.content for p in pending] == ["first", "second"]  # run2 excluded, FIFO


@pytest.mark.asyncio
async def test_mark_consumed_up_to_hides_only_those_at_or_below(db_client):
    repo = SteerInboxRepository(db_client)
    await repo.append(_inj("run1", "m1"))
    await repo.append(_inj("run1", "m2"))
    await repo.append(_inj("run1", "m3"))
    pending = await repo.pull_unconsumed("run1")
    assert len(pending) == 3
    cutoff = pending[1].id  # consume up to and including the second

    consumed = await repo.mark_consumed("run1", cutoff)
    assert consumed == 2

    remaining = await repo.pull_unconsumed("run1")
    assert [p.msg_id for p in remaining] == ["m3"]


@pytest.mark.asyncio
async def test_mark_consumed_is_scoped_to_the_run(db_client):
    repo = SteerInboxRepository(db_client)
    await repo.append(_inj("run1", "m1"))
    await repo.append(_inj("run2", "m1"))
    p1 = await repo.pull_unconsumed("run1")

    await repo.mark_consumed("run1", p1[0].id)

    # run2's row is untouched by consuming run1.
    assert len(await repo.pull_unconsumed("run2")) == 1
