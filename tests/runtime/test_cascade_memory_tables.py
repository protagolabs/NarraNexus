"""
Tests for the SCOPE_USER / SCOPE_INSTANCE / SCOPE_NARRATIVE memory-cascade
gap fix introduced 2026-06-15.

Background — before this fix, `delete_user_cascade` did NOT touch any of
the unified-memory tables:

  memory_event, memory_narrative, memory_chat, memory_entity,
  memory_bus, memory_job, memory_observation, memory_consolidation_queue

These hold LLM-extracted facts about the visitor — name, preferences,
relationships, locations — and they're keyed by `(agent_id, scope_type,
scope_id)` with three user-bearing scopes:

  - SCOPE_USER       scope_id == user_id              (per-visitor PII)
  - SCOPE_INSTANCE   scope_id ∈ user's instance_ids   (per-instance state)
  - SCOPE_NARRATIVE  scope_id ∈ user's narrative_ids  (per-thread facts)

The fix adds Step 4.5 to the cascade: 8 tables × 3 scope axes = 24
DELETEs per user. SCOPE_AGENT / SCOPE_GLOBAL rows stay because they're
not user-specific.
"""
from __future__ import annotations

import pytest


# ─── Constants surface ───────────────────────────────────────────────────────


class TestMemoryTablesConstant:
    def test_seven_memory_kind_tables(self):
        from xyz_agent_context.utils.user_cascade import MEMORY_KIND_TABLES
        assert len(MEMORY_KIND_TABLES) == 7
        assert set(MEMORY_KIND_TABLES) == {
            "memory_event",
            "memory_narrative",
            "memory_chat",
            "memory_entity",
            "memory_bus",
            "memory_job",
            "memory_observation",
        }

    def test_consolidation_queue_named(self):
        from xyz_agent_context.utils.user_cascade import MEMORY_QUEUE_TABLE
        assert MEMORY_QUEUE_TABLE == "memory_consolidation_queue"

    def test_kind_tables_match_schema_registry(self):
        # Drift check: if MEMORY_KINDS grows in schema_registry, the
        # cascade constant must keep up. Same set, modulo prefix.
        from xyz_agent_context.utils.user_cascade import MEMORY_KIND_TABLES
        from xyz_agent_context.utils.schema_registry import MEMORY_KINDS
        from_schema = {f"memory_{k}" for k in MEMORY_KINDS}
        assert set(MEMORY_KIND_TABLES) == from_schema


# ─── _delete_memory_by_scopes — counting + dispatch ─────────────────────────


class _StubDb:
    """Record every (table, filters) tuple delete() is called with, and
    return a per-call configurable rows-deleted value (default 1)."""

    def __init__(self, rows_per_call: int = 1):
        self.calls: list[tuple[str, dict]] = []
        self.rows_per_call = rows_per_call
        self.raises_on: set[tuple[str, str, str]] = set()

    async def delete(self, table: str, filters: dict) -> int:
        key = (table, filters.get("scope_type"), filters.get("scope_id"))
        self.calls.append((table, dict(filters)))
        if key in self.raises_on:
            raise RuntimeError(f"simulated failure on {key}")
        return self.rows_per_call


@pytest.mark.asyncio
class TestDeleteMemoryByScopes:
    async def test_dispatches_to_eight_tables_three_axes(self):
        from xyz_agent_context.utils.user_cascade import _delete_memory_by_scopes
        db = _StubDb(rows_per_call=1)
        result = await _delete_memory_by_scopes(
            db,
            user_id="user_alice",
            instance_ids=["inst_1", "inst_2"],
            narrative_ids=["nar_a"],
        )
        # 8 tables × (1 user + 2 instance + 1 narrative) = 32 DELETE calls.
        assert len(db.calls) == 8 * (1 + 2 + 1)

        # Per table, three reporting keys.
        result_keys = set(result.keys())
        expected_tables = {
            "memory_event", "memory_narrative", "memory_chat",
            "memory_entity", "memory_bus", "memory_job",
            "memory_observation", "memory_consolidation_queue",
        }
        for t in expected_tables:
            for suffix in ("__user", "__instance", "__narrative"):
                assert f"{t}{suffix}" in result_keys

    async def test_scope_user_uses_user_id_directly(self):
        from xyz_agent_context.utils.user_cascade import _delete_memory_by_scopes
        db = _StubDb()
        await _delete_memory_by_scopes(
            db,
            user_id="user_alice",
            instance_ids=[],
            narrative_ids=[],
        )
        # Every user-scoped call uses scope_id=user_id literally.
        user_calls = [c for c in db.calls if c[1].get("scope_type") == "user"]
        assert len(user_calls) == 8  # 7 kinds + queue
        for _table, filters in user_calls:
            assert filters["scope_id"] == "user_alice"

    async def test_empty_instance_and_narrative_ids_produce_zero_calls(self):
        from xyz_agent_context.utils.user_cascade import _delete_memory_by_scopes
        db = _StubDb()
        result = await _delete_memory_by_scopes(
            db,
            user_id="user_alice",
            instance_ids=[],
            narrative_ids=[],
        )
        # Only SCOPE_USER calls fire (1 per table).
        assert len(db.calls) == 8
        # Instance / narrative buckets count zero, not negative.
        for table in (
            "memory_event", "memory_narrative", "memory_chat",
            "memory_entity", "memory_bus", "memory_job",
            "memory_observation", "memory_consolidation_queue",
        ):
            assert result[f"{table}__instance"] == 0
            assert result[f"{table}__narrative"] == 0

    async def test_single_table_failure_does_not_block_others(self):
        from xyz_agent_context.utils.user_cascade import _delete_memory_by_scopes
        db = _StubDb()
        # Simulate memory_observation/SCOPE_USER failing — everything
        # else should still go through.
        db.raises_on.add(("memory_observation", "user", "user_alice"))
        result = await _delete_memory_by_scopes(
            db,
            user_id="user_alice",
            instance_ids=["inst_1"],
            narrative_ids=[],
        )
        assert result["memory_observation__user"] == -1
        # All other tables/scopes for the same user still succeeded.
        assert result["memory_event__user"] == 1
        assert result["memory_consolidation_queue__user"] == 1
        # And the same-table SCOPE_INSTANCE call still ran.
        inst_calls = [
            c for c in db.calls
            if c[0] == "memory_observation"
            and c[1].get("scope_type") == "instance"
        ]
        assert len(inst_calls) == 1


# ─── delete_user_cascade — integration with the new step ─────────────────────


class _FullCascadeStubDb:
    """Stub DB rich enough to drive `delete_user_cascade` end-to-end
    without a real SQLite. Records every operation and returns
    well-formed empty results so the cascade walks every code path."""

    def __init__(self):
        self.deletes: list[tuple[str, dict]] = []

    async def execute(self, sql, params=None, *args, **kwargs):
        # Cascade only execute()s for snapshot queries (instance_ids
        # and narrative_info JSON scan). We return empty so it walks
        # the "no instances / no narratives" code path — that path
        # still must dispatch SCOPE_USER deletes to every memory table,
        # which is the assertion below.
        return []

    async def delete(self, table, filters):
        self.deletes.append((table, dict(filters)))
        return 0

    async def get_one(self, table, filters):
        return None


@pytest.mark.asyncio
class TestDeleteUserCascadeIntegration:
    async def test_cascade_runs_step_4_5_and_emits_memory_keys(self):
        from xyz_agent_context.utils.user_cascade import delete_user_cascade
        db = _FullCascadeStubDb()
        cascade = await delete_user_cascade(
            "user_alice", db, include_workspace=False,
        )
        # SCOPE_USER deletes against every memory table appear in the
        # operation log even when there are zero instances/narratives.
        scope_user_tables = [
            d[0] for d in db.deletes
            if d[1].get("scope_type") == "user"
        ]
        for expected in (
            "memory_event", "memory_observation",
            "memory_consolidation_queue",
        ):
            assert expected in scope_user_tables, (
                f"{expected}: SCOPE_USER delete missing from cascade"
            )
        # Cascade result dict surfaces the new keys (so a caller doing
        # GDPR audit can confirm every memory table was visited).
        assert "memory_observation__user" in cascade
        assert "memory_consolidation_queue__user" in cascade
