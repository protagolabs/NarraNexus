"""
@file_name: test_integration_sqlite.py
@author: NexusAgent
@date: 2026-04-02
@description: End-to-end integration tests using SQLiteBackend with :memory: database

Covers full CRUD lifecycle, JSON field handling, order preservation,
indexed query performance, and transaction atomicity.
"""

from __future__ import annotations

import json

import pytest

from xyz_agent_context.utils.db_backend_sqlite import SQLiteBackend


@pytest.fixture
async def db():
    """Provide an initialized in-memory SQLiteBackend."""
    backend = SQLiteBackend(":memory:")
    await backend.initialize()
    yield backend
    await backend.close()


# --- 1. Agent CRUD lifecycle ---

CREATE_AGENTS_TABLE = """
CREATE TABLE agents (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    agent_id TEXT UNIQUE,
    agent_name TEXT,
    created_by TEXT,
    agent_description TEXT,
    is_public INTEGER DEFAULT 0,
    agent_metadata TEXT
)
"""


class TestAgentCRUDLifecycle:
    """Full CRUD lifecycle test for agents table."""

    @pytest.fixture(autouse=True)
    async def setup_table(self, db):
        """Create the agents table before each test."""
        await db.execute_write(CREATE_AGENTS_TABLE)

    async def test_insert_and_read(self, db):
        """Insert an agent and read it back."""
        await db.insert("agents", {
            "agent_id": "agt_001",
            "agent_name": "TestBot",
            "created_by": "user_1",
            "agent_description": "A test agent",
            "is_public": 0,
            "agent_metadata": json.dumps({"lang": "en"}),
        })

        row = await db.get_one("agents", {"agent_id": "agt_001"})
        assert row is not None
        assert row["agent_name"] == "TestBot"
        assert row["created_by"] == "user_1"
        assert row["is_public"] == 0

    async def test_update(self, db):
        """Update an agent's fields."""
        await db.insert("agents", {
            "agent_id": "agt_002",
            "agent_name": "Original",
            "created_by": "user_1",
        })

        affected = await db.update(
            "agents",
            {"agent_id": "agt_002"},
            {"agent_name": "Updated", "agent_description": "Now described"},
        )
        assert affected == 1

        row = await db.get_one("agents", {"agent_id": "agt_002"})
        assert row["agent_name"] == "Updated"
        assert row["agent_description"] == "Now described"

    async def test_upsert_existing(self, db):
        """Upsert updates an existing row on conflict."""
        await db.insert("agents", {
            "agent_id": "agt_003",
            "agent_name": "V1",
            "created_by": "user_1",
        })

        await db.upsert("agents", {
            "agent_id": "agt_003",
            "agent_name": "V2",
            "created_by": "user_1",
            "agent_description": "Upserted",
        }, "agent_id")

        row = await db.get_one("agents", {"agent_id": "agt_003"})
        assert row["agent_name"] == "V2"
        assert row["agent_description"] == "Upserted"

        # Should still be one row
        all_rows = await db.get("agents", {"agent_id": "agt_003"})
        assert len(all_rows) == 1

    async def test_upsert_new(self, db):
        """Upsert inserts a new row when no conflict."""
        await db.upsert("agents", {
            "agent_id": "agt_new",
            "agent_name": "Brand New",
            "created_by": "user_2",
        }, "agent_id")

        row = await db.get_one("agents", {"agent_id": "agt_new"})
        assert row is not None
        assert row["agent_name"] == "Brand New"

    async def test_delete(self, db):
        """Delete an agent and verify it is gone."""
        await db.insert("agents", {
            "agent_id": "agt_del",
            "agent_name": "ToDelete",
            "created_by": "user_1",
        })

        deleted = await db.delete("agents", {"agent_id": "agt_del"})
        assert deleted == 1

        row = await db.get_one("agents", {"agent_id": "agt_del"})
        assert row is None


# --- 2. Narrative with JSON fields ---

CREATE_NARRATIVES_TABLE = """
CREATE TABLE narratives (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    narrative_id TEXT UNIQUE,
    agent_id TEXT NOT NULL,
    narrative_info TEXT,
    updated_at TEXT
)
"""


class TestNarrativeWithJSON:
    """Test inserting and reading JSON data in narrative_info."""

    @pytest.fixture(autouse=True)
    async def setup_table(self, db):
        await db.execute_write(CREATE_NARRATIVES_TABLE)

    async def test_json_roundtrip(self, db):
        """Insert JSON narrative_info and parse it back."""
        info = {
            "title": "Project Alpha",
            "participants": ["Alice", "Bob"],
            "context": {"depth": 3, "tags": ["urgent", "review"]},
        }

        await db.insert("narratives", {
            "narrative_id": "nar_001",
            "agent_id": "agt_001",
            "narrative_info": info,
            "updated_at": "2026-04-02T10:00:00",
        })

        row = await db.get_one("narratives", {"narrative_id": "nar_001"})
        assert row is not None
        parsed = json.loads(row["narrative_info"])
        assert parsed["title"] == "Project Alpha"
        assert parsed["participants"] == ["Alice", "Bob"]
        assert parsed["context"]["depth"] == 3

    async def test_null_json_field(self, db):
        """Null JSON field is stored and read as None."""
        await db.insert("narratives", {
            "narrative_id": "nar_002",
            "agent_id": "agt_001",
            "narrative_info": None,
        })

        row = await db.get_one("narratives", {"narrative_id": "nar_002"})
        assert row["narrative_info"] is None


# --- 3. get_by_ids preserves order ---

class TestGetByIdsOrder:
    """Verify get_by_ids returns results in the requested order."""

    @pytest.fixture(autouse=True)
    async def setup_table(self, db):
        await db.execute_write(CREATE_AGENTS_TABLE)

    async def test_order_preserved(self, db):
        """Insert 5 agents, request in non-sequential order, verify order matches."""
        ids = ["agt_a", "agt_b", "agt_c", "agt_d", "agt_e"]
        for agent_id in ids:
            await db.insert("agents", {
                "agent_id": agent_id,
                "agent_name": f"Agent {agent_id}",
                "created_by": "user_1",
            })

        # Request in a shuffled order
        request_order = ["agt_d", "agt_a", "agt_e", "agt_b", "agt_c"]
        results = await db.get_by_ids("agents", "agent_id", request_order)

        assert len(results) == 5
        for i, expected_id in enumerate(request_order):
            assert results[i] is not None
            assert results[i]["agent_id"] == expected_id

    async def test_missing_ids_return_none(self, db):
        """Missing IDs return None in the correct position."""
        await db.insert("agents", {
            "agent_id": "agt_exists",
            "agent_name": "Exists",
            "created_by": "user_1",
        })

        results = await db.get_by_ids(
            "agents", "agent_id", ["agt_missing", "agt_exists", "agt_gone"]
        )
        assert len(results) == 3
        assert results[0] is None
        assert results[1]["agent_id"] == "agt_exists"
        assert results[2] is None


# --- 4. Indexed query performance ---

class TestIndexedQueryPerformance:
    """Test indexed query with filter, order, and limit."""

    @pytest.fixture(autouse=True)
    async def setup_table(self, db):
        await db.execute_write(CREATE_NARRATIVES_TABLE)
        await db.execute_write(
            "CREATE INDEX idx_nar_agent_updated ON narratives(agent_id, updated_at DESC)"
        )

    async def test_filtered_ordered_limited_query(self, db):
        """Insert 20 narratives, filter by agent_id, order by updated_at DESC, limit 5."""
        for i in range(20):
            agent = "agt_A" if i < 12 else "agt_B"
            await db.insert("narratives", {
                "narrative_id": f"nar_{i:03d}",
                "agent_id": agent,
                "narrative_info": json.dumps({"index": i}),
                "updated_at": f"2026-04-{(i+1):02d}T00:00:00",
            })

        # Query agent_A narratives, most recent first, limit 5
        results = await db.get(
            "narratives",
            filters={"agent_id": "agt_A"},
            order_by="updated_at DESC",
            limit=5,
        )

        assert len(results) == 5
        # Verify descending order
        for j in range(len(results) - 1):
            assert results[j]["updated_at"] >= results[j + 1]["updated_at"]

    async def test_index_is_used(self, db):
        """Verify the index exists via EXPLAIN QUERY PLAN."""
        for i in range(5):
            await db.insert("narratives", {
                "narrative_id": f"nar_idx_{i}",
                "agent_id": "agt_A",
                "updated_at": f"2026-04-{(i+1):02d}T00:00:00",
            })

        plan = await db.execute(
            'EXPLAIN QUERY PLAN SELECT * FROM narratives WHERE agent_id = ? ORDER BY updated_at DESC LIMIT 5',
            ("agt_A",),
        )
        # The plan should reference the index
        plan_text = str(plan)
        assert "idx_nar_agent_updated" in plan_text


# --- 5. Transaction atomicity ---

class TestTransactionAtomicity:
    """Verify transaction begin/rollback semantics."""

    @pytest.fixture(autouse=True)
    async def setup_table(self, db):
        await db.execute_write(CREATE_AGENTS_TABLE)

    async def test_rollback_discards_inserts(self, db):
        """Begin, insert 2 rows, rollback, verify none persisted."""
        await db.begin_transaction()

        await db.insert("agents", {
            "agent_id": "agt_tx1",
            "agent_name": "TxAgent1",
            "created_by": "user_1",
        })
        await db.insert("agents", {
            "agent_id": "agt_tx2",
            "agent_name": "TxAgent2",
            "created_by": "user_1",
        })

        await db.rollback()

        # Both rows should be gone
        row1 = await db.get_one("agents", {"agent_id": "agt_tx1"})
        row2 = await db.get_one("agents", {"agent_id": "agt_tx2"})
        assert row1 is None
        assert row2 is None

    async def test_commit_persists_inserts(self, db):
        """Begin, insert 2 rows, commit, verify both persisted."""
        await db.begin_transaction()

        await db.insert("agents", {
            "agent_id": "agt_cm1",
            "agent_name": "CommitAgent1",
            "created_by": "user_1",
        })
        await db.insert("agents", {
            "agent_id": "agt_cm2",
            "agent_name": "CommitAgent2",
            "created_by": "user_1",
        })

        await db.commit()

        row1 = await db.get_one("agents", {"agent_id": "agt_cm1"})
        row2 = await db.get_one("agents", {"agent_id": "agt_cm2"})
        assert row1 is not None
        assert row2 is not None
