"""
@file_name: test_embedding_migration_multi_tenant.py
@author: Bin Liang
@date: 2026-04-20
@description: Multi-tenant correctness tests for EmbeddingMigrationService.

Bug 11 (cloud) regression: the migration service and its global `_progress`
singleton were written for single-user desktop. On cloud the service must:
  - Count / rebuild only rows that belong to a specific user_id
  - Keep a per-user progress snapshot so concurrent rebuilds don't stomp
  - Resolve the embedding model from that user's provider slots, not the
    last-loaded global `embedding_config`

These tests pin those invariants in place.
"""
from __future__ import annotations

from typing import Any
from datetime import datetime, timezone

import pytest

from xyz_agent_context.services import embedding_migration_service as mig_mod
from xyz_agent_context.services.embedding_migration_service import (
    EmbeddingMigrationService,
    MigrationProgress,
    get_migration_progress,
)


async def _seed_agent(db, *, agent_id: str, created_by: str) -> None:
    await db.insert(
        "agents",
        {
            "agent_id": agent_id,
            "agent_name": agent_id,
            "created_by": created_by,
            "agent_type": "general",
            "is_public": 0,
        },
    )


async def _seed_narrative(db, *, narrative_id: str, agent_id: str) -> None:
    await db.insert(
        "narratives",
        {
            "narrative_id": narrative_id,
            "type": "chat",
            "agent_id": agent_id,
            "narrative_info": "{}",
            "topic_hint": f"hint-{narrative_id}",
        },
    )


async def _seed_event(
    db, *, event_id: str, agent_id: str, user_id: str, text: str
) -> None:
    await db.insert(
        "events",
        {
            "event_id": event_id,
            "trigger": "chat",
            "trigger_source": "test",
            "agent_id": agent_id,
            "user_id": user_id,
            "embedding_text": text,
            "final_output": text,
        },
    )


async def _seed_job(
    db, *, job_id: str, instance_id: str, agent_id: str, user_id: str
) -> None:
    await db.insert(
        "instance_jobs",
        {
            "instance_id": instance_id,
            "job_id": job_id,
            "agent_id": agent_id,
            "user_id": user_id,
            "title": f"job {job_id}",
            "description": "desc",
            "payload": "",
            "job_type": "test",
            "status": "pending",
        },
    )


async def _seed_instance(
    db, *, instance_id: str, agent_id: str, user_id: str
) -> None:
    await db.insert(
        "module_instances",
        {
            "instance_id": instance_id,
            "agent_id": agent_id,
            "module_class": "SocialNetworkModule",
            "user_id": user_id,
            "status": "active",
        },
    )


async def _seed_entity(
    db, *, instance_id: str, entity_id: str, name: str
) -> None:
    await db.insert(
        "instance_social_entities",
        {
            "instance_id": instance_id,
            "entity_id": entity_id,
            "entity_type": "person",
            "entity_name": name,
            "entity_description": f"desc of {name}",
        },
    )


@pytest.fixture
def patched_embedding(monkeypatch):
    """Replace the live embedding call with a deterministic stub."""

    async def _fake_embed(text: str, model: str = ""):  # noqa: ARG001
        # 4-dim vector, values derived from text length so collisions are unlikely
        n = len(text) or 1
        return [float(n), float(n) + 1, float(n) + 2, float(n) + 3]

    monkeypatch.setattr(mig_mod, "get_embedding", _fake_embed)


@pytest.fixture(autouse=True)
def reset_progress():
    mig_mod._reset_progress_for_tests()
    yield
    mig_mod._reset_progress_for_tests()


@pytest.fixture
def force_new_embedding_path(monkeypatch):
    """Make `use_embedding_store(user_id)` return True in migration context."""
    monkeypatch.setattr(
        mig_mod,
        "_resolve_use_embedding_store",
        lambda user_id: True,
    )


@pytest.fixture
def patched_model_resolver(monkeypatch):
    async def _resolve(user_id: str) -> str:
        return {
            "alice": "model-a",
            "bob": "model-b",
        }.get(user_id, "model-default")

    monkeypatch.setattr(mig_mod, "_resolve_user_embedding_model", _resolve)


@pytest.mark.asyncio
async def test_status_sees_only_caller_user_data(
    db_client, patched_embedding, force_new_embedding_path, patched_model_resolver
):
    await _seed_agent(db_client, agent_id="agent_a", created_by="alice")
    await _seed_agent(db_client, agent_id="agent_b", created_by="bob")
    # Alice gets 2 narratives, Bob 1
    await _seed_narrative(db_client, narrative_id="nar_alice_1", agent_id="agent_a")
    await _seed_narrative(db_client, narrative_id="nar_alice_2", agent_id="agent_a")
    await _seed_narrative(db_client, narrative_id="nar_bob_1", agent_id="agent_b")

    svc_alice = EmbeddingMigrationService(db_client, user_id="alice")
    svc_bob = EmbeddingMigrationService(db_client, user_id="bob")

    status_alice = await svc_alice.get_status()
    status_bob = await svc_bob.get_status()

    assert status_alice["model"] == "model-a"
    assert status_bob["model"] == "model-b"
    assert status_alice["stats"]["narrative"]["total"] == 2
    assert status_bob["stats"]["narrative"]["total"] == 1


@pytest.mark.asyncio
async def test_status_filters_events_by_user_id(
    db_client, patched_embedding, force_new_embedding_path, patched_model_resolver
):
    await _seed_agent(db_client, agent_id="agent_a", created_by="alice")
    await _seed_agent(db_client, agent_id="agent_b", created_by="bob")
    await _seed_event(db_client, event_id="evt_1", agent_id="agent_a", user_id="alice", text="a1")
    await _seed_event(db_client, event_id="evt_2", agent_id="agent_a", user_id="alice", text="a2")
    await _seed_event(db_client, event_id="evt_3", agent_id="agent_b", user_id="bob",   text="b1")

    stats_alice = (await EmbeddingMigrationService(db_client, user_id="alice").get_status())["stats"]
    stats_bob   = (await EmbeddingMigrationService(db_client, user_id="bob").get_status())["stats"]

    assert stats_alice["event"]["total"] == 2
    assert stats_bob["event"]["total"] == 1


@pytest.mark.asyncio
async def test_status_filters_jobs_by_user_id(
    db_client, patched_embedding, force_new_embedding_path, patched_model_resolver
):
    await _seed_agent(db_client, agent_id="agent_a", created_by="alice")
    await _seed_agent(db_client, agent_id="agent_b", created_by="bob")
    await _seed_job(db_client, job_id="job_1", instance_id="inst_a", agent_id="agent_a", user_id="alice")
    await _seed_job(db_client, job_id="job_2", instance_id="inst_b", agent_id="agent_b", user_id="bob")

    stats_alice = (await EmbeddingMigrationService(db_client, user_id="alice").get_status())["stats"]
    stats_bob   = (await EmbeddingMigrationService(db_client, user_id="bob").get_status())["stats"]

    assert stats_alice["job"]["total"] == 1
    assert stats_bob["job"]["total"] == 1


@pytest.mark.asyncio
async def test_status_filters_entities_via_instance_user(
    db_client, patched_embedding, force_new_embedding_path, patched_model_resolver
):
    await _seed_agent(db_client, agent_id="agent_a", created_by="alice")
    await _seed_agent(db_client, agent_id="agent_b", created_by="bob")
    await _seed_instance(db_client, instance_id="inst_a", agent_id="agent_a", user_id="alice")
    await _seed_instance(db_client, instance_id="inst_b", agent_id="agent_b", user_id="bob")
    await _seed_entity(db_client, instance_id="inst_a", entity_id="ent_1", name="Alice's friend")
    await _seed_entity(db_client, instance_id="inst_a", entity_id="ent_2", name="Alice's colleague")
    await _seed_entity(db_client, instance_id="inst_b", entity_id="ent_3", name="Bob's contact")

    stats_alice = (await EmbeddingMigrationService(db_client, user_id="alice").get_status())["stats"]
    stats_bob   = (await EmbeddingMigrationService(db_client, user_id="bob").get_status())["stats"]

    assert stats_alice["entity"]["total"] == 2
    assert stats_bob["entity"]["total"] == 1


@pytest.mark.asyncio
async def test_rebuild_only_touches_caller_user(
    db_client, patched_embedding, force_new_embedding_path, patched_model_resolver
):
    await _seed_agent(db_client, agent_id="agent_a", created_by="alice")
    await _seed_agent(db_client, agent_id="agent_b", created_by="bob")
    await _seed_narrative(db_client, narrative_id="nar_alice_1", agent_id="agent_a")
    await _seed_narrative(db_client, narrative_id="nar_bob_1", agent_id="agent_b")

    svc_alice = EmbeddingMigrationService(db_client, user_id="alice")
    await svc_alice.rebuild_all()

    rows = await db_client.get(
        "embeddings_store",
        filters={"entity_type": "narrative"},
    )
    entity_ids = {r["entity_id"] for r in rows}
    assert "nar_alice_1" in entity_ids
    assert "nar_bob_1" not in entity_ids, (
        "Alice's rebuild must not touch Bob's narratives"
    )

    # Model recorded must be Alice's
    models_for_alice = {r["model"] for r in rows if r["entity_id"] == "nar_alice_1"}
    assert models_for_alice == {"model-a"}


@pytest.mark.asyncio
async def test_progress_is_isolated_per_user():
    prog_alice = get_migration_progress("alice")
    prog_bob = get_migration_progress("bob")

    assert prog_alice is not prog_bob
    prog_alice.is_running = True
    assert get_migration_progress("alice").is_running is True
    assert get_migration_progress("bob").is_running is False


@pytest.mark.asyncio
async def test_missing_user_id_raises(db_client):
    with pytest.raises(ValueError, match="user_id"):
        EmbeddingMigrationService(db_client, user_id="")  # type: ignore[arg-type]
