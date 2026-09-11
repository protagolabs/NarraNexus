"""
@file_name: test_unread_preview_truncation.py
@author:
@date: 2026-09-11
@description: The unread list must never hand the agent a silently clipped
              message (B-23 / upstream issue #73).

Issue #73: a three-paragraph instruction posted in a team room reached an agent
as "... so people can scan it via" — exactly the first 200 characters of the
original — and the agent, told by its own instructions that its unread messages
"are already in this turn's context. You do not need to fetch them", acted on
the fragment as if it were the whole message. The unread renderer cut every
row at 200 characters with no marker, so a clipped message and a complete one
were indistinguishable to the reader.

Two properties are pinned here: an ordinary multi-paragraph instruction is
rendered whole, and a message too long for the per-row budget says it was cut,
by how much, and how to read the rest.
"""
from __future__ import annotations

from types import SimpleNamespace

from narranexus_plugins.message_bus_module.message_bus_module import (
    UNREAD_PREVIEW_MAX_CHARS,
    MessageBusModule,
)

#: The message from issue #73, verbatim — three paragraphs, ~560 characters.
ISSUE_73_MESSAGE = (
    "With your team, create an appealing website calling on everyone to sign "
    "a petition to ask ShallWeTech to organize one event every day.\n\n"
    "The website should display a QR code so people can scan it via phone, "
    "sign their name on it by handwriting. The website should also display a "
    "button, after users click it, it will display a wall with everyone's "
    "signature on it (live update).\n\n"
    "Do not include any info I did not give u. Localhost first to let me "
    "review, then ask the vercel deploy agent to work with me to deploy it"
)


def _span(rows: list[dict], labels: dict | None = None) -> str:
    module = MessageBusModule.__new__(MessageBusModule)
    module.agent_id = "agent_me"
    ctx = SimpleNamespace(extra_data={
        "bus_unread_messages": rows,
        "bus_unread_total": len(rows),
        "bus_room_labels": labels or {},
    })
    return "\n".join(module._volatile_context_parts(ctx))


def test_a_multi_paragraph_room_instruction_is_rendered_whole():
    span = _span(
        [{"from_agent": "usr_owner", "channel_id": "ch_room",
          "content": ISSUE_73_MESSAGE}],
        {"ch_room": "Web Development"},
    )
    assert ISSUE_73_MESSAGE in span
    # And nothing claims it was cut.
    assert "read_history" not in span


def test_a_short_message_renders_unchanged_with_no_marker():
    span = _span([{"from_agent": "agent_peer", "channel_id": "ch_dm",
                   "content": "ping"}])
    assert "`[from agent_peer]` ping" in span
    assert "cut" not in span.split("ping", 1)[1]


def test_an_over_budget_message_announces_the_cut_and_how_to_read_it():
    long_text = "A" * (UNREAD_PREVIEW_MAX_CHARS + 750)
    span = _span([{"from_agent": "agent_peer", "channel_id": "ch_dm",
                   "content": long_text}])
    row = next(line for line in span.splitlines() if "[from agent_peer]" in line)
    # The shown prefix is exactly the budget — not the whole message.
    assert "A" * UNREAD_PREVIEW_MAX_CHARS in row
    assert "A" * (UNREAD_PREVIEW_MAX_CHARS + 1) not in row
    # The cut is stated with both sizes, and the reader is pointed at the tool
    # that returns the full text.
    assert str(len(long_text)) in row
    assert str(UNREAD_PREVIEW_MAX_CHARS) in row
    assert "read_history" in row


def test_a_message_exactly_at_the_budget_is_not_marked():
    text = "B" * UNREAD_PREVIEW_MAX_CHARS
    span = _span([{"from_agent": "agent_peer", "channel_id": "ch_dm",
                   "content": text}])
    assert text in span
    assert "read_history" not in span


def test_a_multipart_row_keeps_its_part_label_alongside_the_cut_marker():
    long_text = "C" * (UNREAD_PREVIEW_MAX_CHARS + 10)
    span = _span([{"from_agent": "agent_peer", "channel_id": "ch_dm",
                   "content": long_text, "part_index": 2, "part_count": 3}])
    row = next(line for line in span.splitlines() if "[from agent_peer]" in line)
    assert "(part 2/3)" in row
    assert "read_history" in row


def test_the_static_rule_no_longer_promises_every_unread_row_is_complete():
    """The instruction that made the agent trust the fragment must say that a
    long row is shown cut and marked, or the marker contradicts the rules."""
    module = MessageBusModule.__new__(MessageBusModule)
    module.agent_id = "agent_me"
    static = "\n".join(module._static_instruction_parts())
    assert "read_history" in static
    assert "shown cut" in static
