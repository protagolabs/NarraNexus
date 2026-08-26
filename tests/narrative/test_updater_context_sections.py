"""
@file_name: test_updater_context_sections.py
@date: 2026-08-26
@description: C3 pin — the updater context's section set is a closed list.

The action-digest section was removed at a9260baa4^.. after it was measured
renaming continuity anchors (PR2_CROWDING_ANALYSIS: same-line rate 65.3% ->
56.8% on anchor-rewritten rows, McNemar p=0.0002). Pinning the ABSENCE of one
literal heading would go green the day someone re-adds the content under a
new name, so this pins the whole section vocabulary instead: any new input
into the context that continuity reads through the anchor fields must show up
here and justify itself against C3.
"""
from types import SimpleNamespace

import pytest

from xyz_agent_context.narrative._narrative_impl import updater as updater_mod
from xyz_agent_context.narrative.models import Event, EventLogEntry


def _narrative():
    return SimpleNamespace(
        narrative_info=SimpleNamespace(
            name="Group-chat self-introduction thread",
            description="",
            current_summary="Topic: onboarding\nStatus: ongoing",
        ),
        topic_keywords=["onboarding"],
        dynamic_summary=[],
    )


def _event():
    from datetime import datetime, timezone

    return Event(
        id="evt_c3pin000", trigger="chat", trigger_source="user_x",
        env_context={}, module_instances=[],
        event_log=[
            EventLogEntry(
                timestamp=datetime.now(timezone.utc), type="tool_call",
                content={"tool_name": "Read", "arguments": {"file_path": "/tmp/web.log"}},
            ),
        ],
        final_output="Done — findings sent.",
        created_at=datetime.now(timezone.utc), updated_at=datetime.now(timezone.utc),
        agent_id="agent_test", user_id="user_x",
    )


@pytest.mark.asyncio
async def test_update_context_sections_are_a_closed_list():
    upd = updater_mod.NarrativeUpdater("agent_test")

    context = await upd._build_update_context(_narrative(), _event())

    headings = [line for line in context.split("\n") if line.startswith("## ")]
    assert headings == [
        "## Current Narrative Information",
        "## Recent Conversation History",
        "## Latest Conversation",
    ], (
        "a new section reached the updater context: %r — the updater's output "
        "is the continuity anchor (C3, see the tombstone at the top of "
        "updater.py); justify any addition against the anchor-rename evidence "
        "before landing it" % headings
    )
    # And no tool payload leaks in through the surviving sections either.
    assert "web.log" not in context
