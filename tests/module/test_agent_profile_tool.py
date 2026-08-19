"""
@file_name: test_agent_profile_tool.py
@author: NarraNexus
@date: 2026-08-04
@description: Setting an agent's name/description must be one transaction:
the DB row, the agent's own memory, and the peer-discovery row.

Two prod failures (P1 section 02, 2026-08-03) meet in this tool:

① Identity memory residue. The user named their first agent 「凑企鹅」, then at
   08:42/43 handed that same name to a SECOND agent with the rename tool. The
   tool wrote ``agents.agent_name`` and nothing else, so the first agent's
   long-term memory still asserted the old identity and it introduced itself as
   "凑企鹅 is actually my own agent name" (evt_1f9c6680) — while the DB said
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

from xyz_agent_context.module.awareness_module.awareness_module import AwarenessModule
# The identity-note helpers moved to the shared _awareness_writes when
# update_agent_profile was routed through the AgentDataStore seam; import from
# the package surface (which re-exports them).
from xyz_agent_context.module.awareness_module import (
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


def test_a_chain_of_renames_leaves_one_current_record():
    """Each note supersedes the ones naming a different name, so a chain
    collapses to the note that is true now.

    Nothing is lost: the survivor names the previous name itself ("renamed from
    X to Y"). What goes is the contradiction — the incident showed one
    platform-voiced line is enough for the agent to introduce itself by the
    wrong name and defend it, so keeping five mutually exclusive ones and
    trusting the model to prefer the last is the same bet with worse odds.
    """
    profile = "# Agent Awareness Profile\n"
    for i in range(MAX_IDENTITY_CHANGE_ENTRIES + 4):
        profile = merge_identity_change_note(
            profile, build_identity_change_note(f"name{i}", f"name{i + 1}")
        )

    section = profile.split(IDENTITY_CHANGE_SECTION, 1)[1]
    entries = [ln for ln in section.splitlines() if ln.strip().startswith("- ")]
    last = f"name{MAX_IDENTITY_CHANGE_ENTRIES + 4}"
    assert len(entries) == 1, f"superseded records were kept: {entries}"
    assert last in entries[0]
    assert profile.count(IDENTITY_CHANGE_SECTION) == 1, "one section, not one per rename"


def test_records_agreeing_on_the_current_name_are_still_capped():
    """Pruning removes contradictions, not the growth bound.

    Entries asserting the SAME name survive each other — a rename followed by
    reconciliations of the same name is the real case — so the cap is still the
    only thing keeping the section from growing into the context window it
    lives in. Testing it with a chain of DIFFERENT names would pass on a build
    with no cap at all, since pruning alone leaves one entry.
    """
    profile = "# Agent Awareness Profile\n"
    for _ in range(MAX_IDENTITY_CHANGE_ENTRIES + 4):
        profile = merge_identity_change_note(
            profile, build_identity_change_note("old", "same")
        )

    section = profile.split(IDENTITY_CHANGE_SECTION, 1)[1]
    entries = [ln for ln in section.splitlines() if ln.strip().startswith("- ")]
    assert len(entries) == MAX_IDENTITY_CHANGE_ENTRIES


def test_an_empty_profile_still_gets_a_valid_section():
    merged = merge_identity_change_note("", build_identity_change_note("A", "B"))
    assert IDENTITY_CHANGE_SECTION in merged and "- " in merged


def test_content_after_the_section_survives_a_second_rename():
    """The docstring's promise, tested where it can actually break.

    ``update_awareness`` has the model rewrite the WHOLE profile and the prompt
    tells it to keep the full structured format, so the identity section
    routinely ends up in the MIDDLE. A merge that treats "everything after the
    marker" as the section silently ate the sections below it — the exact
    long-term-memory loss this function claims never to cause (found in review,
    2026-08-05: the first three cases all happened to put the section last).
    """
    profile = "\n".join([
        "# Agent Awareness Profile",
        "",
        "## 1. Preferences",
        "- likes short answers",
        "",
        IDENTITY_CHANGE_SECTION,
        "- 2026-08-04: renamed by your creator from A to B.",
        "",
        "## 2. Working Style",
        "- prefers async updates",
        "free-form observation that is not a bullet",
        "",
        "## 3. Role and Identity",
        "- owns the weekly report",
    ])

    merged = merge_identity_change_note(profile, build_identity_change_note("B", "C"))

    # Everything outside the identity section is preserved verbatim.
    for kept in (
        "## 1. Preferences", "- likes short answers",
        "## 2. Working Style", "- prefers async updates",
        "free-form observation that is not a bullet",
        "## 3. Role and Identity", "- owns the weekly report",
    ):
        assert kept in merged, f"lost: {kept!r}"

    # And the rename log is still one section holding both entries, in order.
    assert merged.count(IDENTITY_CHANGE_SECTION) == 1
    section = merged.split(IDENTITY_CHANGE_SECTION, 1)[1].split("\n## ", 1)[0]
    entries = [ln for ln in section.splitlines() if ln.strip().startswith("- ")]
    assert len(entries) == 2
    assert "from A to B" in entries[0], "the pre-existing entry keeps its wording"
    assert "B" in entries[1] and "C" in entries[1], "newest rename appended last"
    # A later section's bullets must not be absorbed as rename entries.
    assert all("async updates" not in e for e in entries)


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

    # The MCP tools open their own connection; point it at this one. The tool
    # now delegates to the AgentDataStore seam, whose DirectStore resolves the
    # db via XYZBaseModule.get_mcp_db_client (the base classmethod) — so patch it
    # on the base, which also covers the AwarenessModule-inherited call.
    async def _client():
        return client

    from xyz_agent_context.module.base import XYZBaseModule
    monkeypatch.setattr(XYZBaseModule, "get_mcp_db_client", _client)
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
        new_description="Reviews lesson plans and Q&A.",
    )

    assert "success" in result.lower()
    row = await db.get_one("agents", {"agent_id": "agent_a"})
    assert row["agent_name"] == "咕咕嘎嘎"
    assert row["agent_description"] == "Reviews lesson plans and Q&A."


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

    profile = await AgentRegistryRepository(db).get_profile("agent_a")
    assert profile is not None
    assert "咕咕嘎嘎" in profile.description
    assert "Reviews lesson plans." in profile.description


@pytest.mark.asyncio
async def test_rewriting_the_same_description_is_not_an_error(db):
    """Dialect trap: ``update_agent`` returns ``cursor.rowcount``, and MySQL
    (dev/prod — the pool sets no CLIENT_FOUND_ROWS) counts CHANGED rows while
    SQLite counts MATCHED ones. Without an equality short-circuit, re-saving an
    identical description returns 0 there and the agent is told
    "Error: the update did not apply" — for a write that was simply a no-op.

    The §5 prompt actively invites repeat calls ("whenever the answer
    changes"), and a model that reads an error usually retries or rewrites,
    so this would be a routine false failure on cloud only (review 2026-08-05).
    """
    await _seed_agent(db, "agent_a", "咕咕嘎嘎", description="Reviews lesson plans.")

    result = await _tool("update_agent_profile")(
        agent_id="agent_a", new_description="Reviews lesson plans."
    )

    assert "error" not in result.lower(), result
    row = await db.get_one("agents", {"agent_id": "agent_a"})
    assert row["agent_description"] == "Reviews lesson plans."


@pytest.mark.asyncio
async def test_same_name_and_same_description_together_are_a_no_op(db):
    await _seed_agent(db, "agent_a", "咕咕嘎嘎", description="Reviews lesson plans.")

    result = await _tool("update_agent_profile")(
        agent_id="agent_a", new_name="咕咕嘎嘎",
        new_description="  Reviews lesson plans.  ",   # padding is not a change
    )

    assert "error" not in result.lower(), result
    assert "no changes" in result.lower()


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


def test_every_identity_note_states_the_current_name_readably():
    """Whatever a note says, `identity_note_asserts` must find the name in it.

    Superseding a stale record depends on reading back what each record claims,
    so a note phrased outside that shape silently asserts nothing: it is
    appended beside the record it was written to replace, and the contradiction
    the whole mechanism exists to remove survives. That is exactly what the
    reconciliation note did on its first draft ("Your name is 「X」").
    """
    from xyz_agent_context.module.awareness_module import (
        build_identity_reconciliation_note,
        identity_note_asserts,
    )

    assert identity_note_asserts(build_identity_change_note("A", "B")) == "B"
    assert (
        identity_note_asserts(build_identity_reconciliation_note("B", "A")) == "B"
    )
