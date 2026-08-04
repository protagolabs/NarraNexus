"""
@file_name: test_agent_profile_tool.py
@author: NarraNexus
@date: 2026-08-04
@description: Setting an agent's name/description must be one transaction:
the DB row, the agent's own memory, and the peer-discovery row.

Two prod failures (P1 段02, 2026-08-03) meet in this tool:

① Identity memory residue. The user named their first agent 「凑企鹅」, then at
   08:42/43 handed that same name to a SECOND agent with the rename tool. The
   tool wrote ``agents.agent_name`` and nothing else, so the first agent's
   long-term memory still asserted the old identity and it introduced itself as
   「凑企鹅 is actually my own agent name」 (evt_1f9c6680) — while the DB said
   otherwise, and a second agent now legitimately held that name.

② A2A discovery death. ``agents.agent_description`` was only ever written at
   creation (as a placeholder) — no rename, config or skill install touched it,
   and the bus registry snapshotted whatever was there.

So the tool that establishes identity has to: write both fields, leave a
machine-written correction in the memory that is injected every turn, tell the
model when a name is already taken (rather than silently creating a duplicate),
and refresh discovery immediately.
"""
from __future__ import annotations

import pytest

from xyz_agent_context.module.awareness_module.awareness_module import (
    AwarenessModule,
    IDENTITY_CHANGE_SECTION,
    MAX_IDENTITY_CHANGE_ENTRIES,
    build_identity_change_note,
    merge_identity_change_note,
)

OWNER = "user_tc"


# ---------------------------------------------------------------------------
# The memory correction (pure functions — no DB)
# ---------------------------------------------------------------------------


def test_note_states_both_names_and_that_the_old_one_is_not_ours():
    note = build_identity_change_note("凑企鹅", "咕咕嘎嘎", when="2026-08-04")

    assert "凑企鹅" in note and "咕咕嘎嘎" in note
    assert "2026-08-04" in note
    # The residue case: memory keeps asserting the old name, so the correction
    # must explicitly retire it rather than only announce the new one.
    assert "no longer" in note.lower()


def test_the_note_is_appended_to_a_profile_not_rewritten_over_it():
    profile = "# Agent Awareness Profile\n\n## 1. Preferences\n- likes short answers\n"

    merged = merge_identity_change_note(profile, build_identity_change_note("A", "B"))

    assert "## 1. Preferences" in merged, "existing profile must survive verbatim"
    assert "- likes short answers" in merged
    assert IDENTITY_CHANGE_SECTION in merged


def test_repeated_renames_do_not_grow_without_bound():
    profile = "# Agent Awareness Profile\n"
    for i in range(MAX_IDENTITY_CHANGE_ENTRIES + 4):
        profile = merge_identity_change_note(
            profile, build_identity_change_note(f"name{i}", f"name{i + 1}")
        )

    section = profile.split(IDENTITY_CHANGE_SECTION, 1)[1]
    entries = [ln for ln in section.splitlines() if ln.strip().startswith("- ")]
    assert len(entries) == MAX_IDENTITY_CHANGE_ENTRIES
    # The newest rename must be the one that survives.
    last = f"name{MAX_IDENTITY_CHANGE_ENTRIES + 4}"
    assert any(last in e for e in entries)
    assert profile.count(IDENTITY_CHANGE_SECTION) == 1, "one section, not one per rename"


def test_an_empty_profile_still_gets_a_valid_section():
    merged = merge_identity_change_note("", build_identity_change_note("A", "B"))
    assert IDENTITY_CHANGE_SECTION in merged and "- " in merged


# ---------------------------------------------------------------------------
# The tool, against a real database
# ---------------------------------------------------------------------------


@pytest.fixture
async def db(monkeypatch):
    from xyz_agent_context.utils.db.database import AsyncDatabaseClient
    from xyz_agent_context.utils.db.db_backend_sqlite import SQLiteBackend
    from xyz_agent_context.utils.db.schema_registry import auto_migrate

    backend = SQLiteBackend(":memory:")
    await backend.initialize()
    await auto_migrate(backend)
    client = await AsyncDatabaseClient.create_with_backend(backend)

    # The MCP tools open their own connection; point it at this one.
    async def _client():
        return client

    monkeypatch.setattr(AwarenessModule, "get_mcp_db_client", _client)
    try:
        yield client
    finally:
        await client.close()


async def _seed_agent(db, agent_id: str, name: str, description: str = "") -> str:
    await db.insert("agents", {
        "agent_id": agent_id, "agent_name": name, "created_by": OWNER,
        "agent_description": description, "is_public": 0,
    })
    instance_id = f"aware_{agent_id[-4:]}"
    await db.insert("module_instances", {
        "instance_id": instance_id, "agent_id": agent_id, "user_id": OWNER,
        "module_class": "AwarenessModule", "status": "active",
    })
    await db.insert("instance_awareness", {
        "instance_id": instance_id,
        "awareness": "# Agent Awareness Profile\n\n## 4. Role and Identity\n- I am "
                     f"{name}\n",
    })
    return instance_id


def _tool(name: str):
    """Pull one registered MCP tool's function out of the module's server."""
    mcp = AwarenessModule(agent_id="agent_a").create_mcp_server()
    for tool in mcp._tool_manager.list_tools():
        if tool.name == name:
            return tool.fn
    raise AssertionError(f"tool {name} not registered")


@pytest.mark.asyncio
async def test_profile_update_writes_both_fields(db):
    await _seed_agent(db, "agent_a", "New Agent")

    result = await _tool("update_agent_profile")(
        agent_id="agent_a", new_name="咕咕嘎嘎",
        new_description="Reviews lesson plans and答疑.",
    )

    assert "success" in result.lower()
    row = await db.get_one("agents", {"agent_id": "agent_a"})
    assert row["agent_name"] == "咕咕嘎嘎"
    assert row["agent_description"] == "Reviews lesson plans and答疑."


@pytest.mark.asyncio
async def test_description_alone_can_be_set_without_touching_the_name(db):
    await _seed_agent(db, "agent_a", "咕咕嘎嘎")

    await _tool("update_agent_profile")(
        agent_id="agent_a", new_description="Reviews lesson plans."
    )

    row = await db.get_one("agents", {"agent_id": "agent_a"})
    assert row["agent_name"] == "咕咕嘎嘎"
    assert row["agent_description"] == "Reviews lesson plans."


@pytest.mark.asyncio
async def test_a_rename_corrects_the_identity_memory(db):
    """① the residue case. The profile is injected verbatim every turn, so the
    correction has to land IN it — otherwise the old self-description keeps
    winning."""
    instance_id = await _seed_agent(db, "agent_a", "凑企鹅")

    await _tool("update_agent_profile")(agent_id="agent_a", new_name="咕咕嘎嘎")

    awareness = (await db.get_one("instance_awareness",
                                  {"instance_id": instance_id}))["awareness"]
    assert IDENTITY_CHANGE_SECTION in awareness
    assert "凑企鹅" in awareness and "咕咕嘎嘎" in awareness
    assert "no longer" in awareness.lower()
    # Pre-existing profile content is untouched.
    assert "## 4. Role and Identity" in awareness


@pytest.mark.asyncio
async def test_setting_only_a_description_writes_no_rename_note(db):
    instance_id = await _seed_agent(db, "agent_a", "咕咕嘎嘎")

    await _tool("update_agent_profile")(
        agent_id="agent_a", new_description="Reviews lesson plans."
    )

    awareness = (await db.get_one("instance_awareness",
                                  {"instance_id": instance_id}))["awareness"]
    assert IDENTITY_CHANGE_SECTION not in awareness


@pytest.mark.asyncio
async def test_renaming_to_the_same_name_is_not_an_identity_change(db):
    instance_id = await _seed_agent(db, "agent_a", "咕咕嘎嘎")

    await _tool("update_agent_profile")(agent_id="agent_a", new_name="咕咕嘎嘎")

    awareness = (await db.get_one("instance_awareness",
                                  {"instance_id": instance_id}))["awareness"]
    assert IDENTITY_CHANGE_SECTION not in awareness


@pytest.mark.asyncio
async def test_a_name_already_held_by_a_sibling_is_reported_not_blocked(db):
    """The hand-off is legitimate (the user did it on purpose), but doing it
    SILENTLY is what produced two agents answering to one name. Allow it, and
    say so — the model can then check with the owner."""
    await _seed_agent(db, "agent_a", "凑企鹅")
    await _seed_agent(db, "agent_b", "New Agent")

    result = await _tool("update_agent_profile")(
        agent_id="agent_b", new_name="凑企鹅"
    )

    assert "agent_a" in result, "the current holder must be named"
    row = await db.get_one("agents", {"agent_id": "agent_b"})
    assert row["agent_name"] == "凑企鹅", "not blocked — the owner may intend this"


@pytest.mark.asyncio
async def test_another_users_agent_never_counts_as_a_name_clash(db):
    await db.insert("agents", {
        "agent_id": "agent_other", "agent_name": "凑企鹅",
        "created_by": "user_someone_else", "agent_description": "", "is_public": 0,
    })
    await _seed_agent(db, "agent_b", "New Agent")

    result = await _tool("update_agent_profile")(agent_id="agent_b", new_name="凑企鹅")

    assert "agent_other" not in result


@pytest.mark.asyncio
async def test_the_profile_update_refreshes_peer_discovery_immediately(db):
    """② target 2: a peer must be able to find the new description without
    waiting for this agent to take another turn."""
    from xyz_agent_context.repository.agent_registry_repository import (
        AgentRegistryRepository,
    )

    await _seed_agent(db, "agent_a", "New Agent")

    await _tool("update_agent_profile")(
        agent_id="agent_a", new_name="咕咕嘎嘎",
        new_description="Reviews lesson plans.",
    )

    profile = await AgentRegistryRepository(db).get("agent_a")
    assert profile is not None
    assert "咕咕嘎嘎" in profile.description
    assert "Reviews lesson plans." in profile.description


@pytest.mark.asyncio
async def test_calling_with_nothing_to_change_is_an_explicit_error(db):
    await _seed_agent(db, "agent_a", "咕咕嘎嘎")

    result = await _tool("update_agent_profile")(agent_id="agent_a")

    assert "error" in result.lower()


@pytest.mark.asyncio
async def test_unknown_agent_is_an_error_not_a_silent_success(db):
    result = await _tool("update_agent_profile")(
        agent_id="agent_missing", new_name="X"
    )
    assert "error" in result.lower()


def test_the_old_name_only_tool_is_gone():
    """No compat shim (iron rule #2): a tool that writes the name alone is the
    bug. If it comes back, the identity-memory correction is bypassable."""
    mcp = AwarenessModule(agent_id="agent_a").create_mcp_server()
    names = {t.name for t in mcp._tool_manager.list_tools()}
    assert "update_agent_profile" in names
    assert "update_agent_name" not in names
