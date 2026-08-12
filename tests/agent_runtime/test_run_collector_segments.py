"""
@file_name: test_run_collector_segments.py
@author: NarraNexus
@date: 2026-08-12
@description: Keeping the monologue/reply boundary that the room needs to read.

In a team room the agent's own thinking is streamed as `AGENT_THINKING` with a
`monologue` payload, and its answer as `AGENT_RESPONSE` deltas. The collector
appended both to one list and joined it, so what reached the wall was a single
markdown blob with the boundary gone.

That is why "reuse the private chat's segmentTurn" cannot work: segmentTurn cuts
a turn using the EVENT STREAM, and by the time a team message exists the event
stream has been flattened away. No frontend heuristic can recover it — it can
only guess, and guessing wrong renders deliberation as conclusion or the reverse.

So the boundary is preserved here, at the one place that still has it.

The most important test in this file is the LAST one: every non-team turn in the
product goes through this same function, and its output must not move by a
single byte.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import AsyncIterator

import pytest

from xyz_agent_context.agent_runtime.run_collector import collect_run
from xyz_agent_context.schema.runtime_message import MessageType


class _FakeRuntime:
    """Same stand-in the sibling collector tests use."""

    def __init__(self, messages: list):
        self._messages = messages

    def run(self, **_kwargs) -> AsyncIterator:
        async def _gen():
            for m in self._messages:
                yield m

        return _gen()


def _thinking(monologue: str = "", text: str = "") -> SimpleNamespace:
    return SimpleNamespace(
        message_type=MessageType.AGENT_THINKING, monologue=monologue, delta=text, raw=None
    )


def _response(delta: str) -> SimpleNamespace:
    return SimpleNamespace(message_type=MessageType.AGENT_RESPONSE, delta=delta, raw=None)


async def _collect(messages: list, *, include_monologue: bool):
    return await collect_run(
        _FakeRuntime(messages),
        agent_id="a",
        user_id="u",
        input_content="hi",
        working_source="chat",
        include_monologue=include_monologue,
    )


# ── the boundary survives ───────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_monologue_and_reply_become_separate_segments():
    out = await _collect(
        [_thinking(monologue="let me check the parser"), _response("the parser is fine")],
        include_monologue=True,
    )

    assert [(s["kind"], s["text"]) for s in out.segments] == [
        ("monologue", "let me check the parser"),
        ("reply", "the parser is fine"),
    ]


@pytest.mark.asyncio
async def test_consecutive_pieces_of_one_kind_merge():
    """Deltas arrive in fragments; one thought should not render as six."""
    out = await _collect(
        [_response("the "), _response("parser "), _response("is fine")],
        include_monologue=True,
    )

    assert len(out.segments) == 1
    assert out.segments[0]["text"] == "the parser is fine"


@pytest.mark.asyncio
async def test_interleaving_is_preserved_in_order():
    """An agent may think, answer, think again. Order is the whole point —
    reordering would attribute a conclusion to the wrong deliberation."""
    out = await _collect(
        [
            _thinking(monologue="first thought"),
            _response("first answer"),
            _thinking(monologue="second thought"),
            _response("second answer"),
        ],
        include_monologue=True,
    )

    assert [s["kind"] for s in out.segments] == ["monologue", "reply", "monologue", "reply"]


@pytest.mark.asyncio
async def test_output_text_is_unchanged_by_segmenting():
    """`content` stays exactly what it was. It is what TEXT consumers read —
    the memory index, and other agents' scrollback — and a rendering problem
    must not rewrite the thing everything else reads."""
    msgs = [_thinking(monologue="thinking"), _response("answering")]

    out = await _collect(msgs, include_monologue=True)

    assert out.output_text == "thinkinganswering"
    assert "".join(s["text"] for s in out.segments) == out.output_text


@pytest.mark.asyncio
async def test_provider_reasoning_is_not_a_segment():
    """Provider CoT arrives as AGENT_THINKING with monologue="" — it is not the
    agent speaking and never reached output_text either."""
    out = await _collect(
        [_thinking(monologue="", text="provider chain of thought"), _response("hi")],
        include_monologue=True,
    )

    assert [s["kind"] for s in out.segments] == ["reply"]


# ── the blast radius ────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_a_non_team_turn_is_byte_identical():
    """EVERY turn in the product goes through this function. A team-room
    rendering feature may not change one byte of what a private chat, a job, or
    a channel turn produces.

    With include_monologue off, the thinking is not the agent speaking, so there
    is nothing to segment and output_text carries only the reply — exactly as
    before this change.
    """
    out = await _collect(
        [_thinking(monologue="should not appear"), _response("the answer")],
        include_monologue=False,
    )

    assert out.output_text == "the answer"
    assert [(s["kind"], s["text"]) for s in out.segments] == [("reply", "the answer")]


@pytest.mark.asyncio
async def test_an_empty_run_produces_no_segments():
    """A silent turn has nothing to lay out, and an empty list must not become
    one empty segment — the wall would render a blank bubble."""
    out = await _collect([], include_monologue=True)

    assert out.segments == []
    assert out.output_text == ""


@pytest.mark.asyncio
async def test_whitespace_only_output_produces_no_segments():
    """`if response_text:` upstream already drops a whitespace-only turn; the
    segments must agree with it rather than resurrect an empty bubble."""
    out = await _collect([_response("   ")], include_monologue=True)

    assert out.segments == []
