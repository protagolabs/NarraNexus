"""
@file_name: test_team_reply_accounting.py
@author:
@date: 2026-08-12
@description: A team reply is a delivery, and the turn's books must say so.

Everywhere else a reply leaves the process through a tool call, which is what
`_delivered_to_origin` looks for. A team room does not work that way: the
agent's plain text IS the reply and the platform posts it. The reply surface is
emptied outright for those turns, so no tool frame is even reachable — the
accounting was not merely missing it, it was structurally incapable of seeing
it. Every team turn therefore filed as "no reply sent", and the row it wrote was
an `activity` row, which the next turn's history loader drops. Each turn in a
team room started cold.

The fix cannot be "synthesise the frame and hope". Step 3 already states the
rule for the one other platform-delivered surface: the frame is emitted ONLY
after the channel confirms the send, because recording "replied" for a message
that never left the process is the same class of lie. So the delivery moves
INTO the turn as a callback, and the frame follows its result.

Pinned here:
  * a delivered team reply produces a frame `_delivered_to_origin` accepts
  * a REFUSED delivery produces none — the books stay honest when the post fails
  * a silent turn stays silent
  * the frame never counts as owner-visible: that would re-anchor the owner's
    session on every team reply, which is a bug this repo has already fixed once
"""
from __future__ import annotations

import pytest


PLATFORM_KEY = "_platform_reply_text"


def _frames(agent_loop_response):
    """Tool-ish frames the accounting layer would inspect."""
    out = []
    for r in agent_loop_response:
        details = getattr(r, "details", None) or {}
        if details.get("tool_name"):
            out.append(details)
    return out


@pytest.mark.asyncio
async def test_a_delivered_team_reply_is_recorded_as_delivered():
    from xyz_agent_context.agent_runtime._agent_runtime_steps.step_3_agent_loop import (  # noqa: E501
        _post_team_room_reply,
    )

    posted: list[str] = []

    async def _deliver(text: str) -> bool:
        posted.append(text)
        return True

    landed = await _post_team_room_reply(
        final_output="the OCR is done", deliver=_deliver
    )

    assert posted == ["the OCR is done"]
    assert landed is True
    # And the frame the caller builds on that result carries the real text.
    from xyz_agent_context.agent_runtime._agent_runtime_steps.step_3_agent_loop import (  # noqa: E501
        _team_room_reply_frame,
    )

    details = _frames([_team_room_reply_frame("the OCR is done", "ch_1")])
    assert len(details) == 1
    assert "bus_send_message" in details[0]["tool_name"]
    assert details[0]["arguments"][PLATFORM_KEY] == "the OCR is done"


@pytest.mark.asyncio
async def test_a_refused_delivery_records_nothing():
    """The whole reason this is a callback and not a synthetic frame. If the
    room never received it, the turn did not reply — and a memory row saying
    otherwise is exactly the lie being removed."""
    from xyz_agent_context.agent_runtime._agent_runtime_steps.step_3_agent_loop import (  # noqa: E501
        _post_team_room_reply,
    )

    async def _deliver(text: str) -> bool:
        return False

    assert await _post_team_room_reply(
        final_output="never made it", deliver=_deliver
    ) is False


@pytest.mark.asyncio
async def test_a_silent_turn_delivers_nothing():
    from xyz_agent_context.agent_runtime._agent_runtime_steps.step_3_agent_loop import (  # noqa: E501
        _post_team_room_reply,
    )

    called = False

    async def _deliver(text: str) -> bool:
        nonlocal called
        called = True
        return True

    assert await _post_team_room_reply(
        final_output="   ", deliver=_deliver
    ) is False
    assert called is False


@pytest.mark.asyncio
async def test_a_delivery_that_raises_does_not_record_a_reply():
    """A post that blew up is a post that did not happen."""
    from xyz_agent_context.agent_runtime._agent_runtime_steps.step_3_agent_loop import (  # noqa: E501
        _post_team_room_reply,
    )

    async def _deliver(text: str) -> bool:
        raise RuntimeError("bus down")

    assert await _post_team_room_reply(
        final_output="something", deliver=_deliver
    ) is False


def test_the_team_frame_is_not_owner_visible():
    """Load-bearing invariant, and the reason it gets its own test.

    `bus_send_message` counts as "delivered to whoever contacted you" but NOT
    as "the owner saw it" — the split exists because otherwise every
    agent-to-agent reply re-anchors the owner's chat session. The team frame
    rides that same tool name, so it inherits the right answer; a later change
    that promotes it to the owner-visible list would silently reintroduce the
    bug, and the cold-start work planned on top of this depends on it holding.
    """
    import xyz_agent_context.message_bus  # noqa: F401 — registers the handler
    from xyz_agent_context.channel.message_source_handler import (
        MessageSourceRegistry,
    )

    handler = MessageSourceRegistry.get("message_bus")
    # The full tool name step 3 actually emits — asserting the bare stem would
    # not even prove the prefix matching works.
    tool = "mcp__message_bus_module__bus_send_message"

    # Through the real consumers, NOT the raw fields. The natural way to break
    # this invariant is to delete the `owner_visible_reply_tool_names=` line, at
    # which point the field is None and `effective_owner_visible_names` falls
    # back to the full reply list — reviving the bug. An earlier version of this
    # test asserted `"bus_send_message" not in (field or [])`, which stays green
    # through exactly that change: `None or []` is empty, so the assertion is
    # vacuous precisely when it matters.
    assert handler.is_user_reply_tool(tool) is True
    assert handler.is_owner_visible_reply_tool(tool) is False


# ── when a turn may NOT speak in the room ───────────────────────────────────
#
# The delivery point sits after the loop's try/except, so every reason not to
# deliver has to be checked there explicitly. These drive the real predicate —
# an earlier version of this test re-implemented the condition inside the test,
# which would have stayed green with the production gate deleted.

def _gate(**kw):
    from xyz_agent_context.agent_runtime._agent_runtime_steps.step_3_agent_loop import (  # noqa: E501
        _should_deliver_team_reply,
    )

    return _should_deliver_team_reply(**{
        "has_deliverer": True, "hit_fatal": False, "cancelled": False, **kw
    })


def test_an_ordinary_team_turn_delivers():
    assert _gate() == (True, "")


def test_a_failed_loop_does_not_post_a_half_sentence():
    """`final_output` on a fatal path holds whatever streamed before the break.
    Posting it puts an unmarked fragment in front of the room, where it reads as
    an answer; the failure is surfaced separately, by the trigger, as the room."""
    deliver, reason = _gate(hit_fatal=True)

    assert deliver is False
    assert reason == "loop_failed"


def test_a_stopped_turn_does_not_speak_in_the_room():
    """Stop must mean nothing leaves the process.

    `CancelledByUser` is raised only after step 4 so an interrupted turn still
    reaches history — which means this code always runs and has to check for
    itself. The cost of missing it is not a stray line: the post path parses
    @mentions, so a turn the user killed could wake teammates into full runs.
    """
    deliver, reason = _gate(cancelled=True)

    assert deliver is False
    assert reason == "cancelled_by_owner"


def test_a_non_team_turn_is_not_a_skip_worth_logging():
    """Every chat/job/IM turn takes this path; it is the normal case, not an
    event."""
    assert _gate(has_deliverer=False) == (False, "not_a_team_room")


# ── "failed" must mean the same thing in both places ────────────────────────

def test_a_returned_fatal_counts_even_though_nothing_was_raised():
    """`auth_expired` and `config_actionable` are marked fatal and RETURNED as
    frames, so the loop never throws. A gate reading only "did it raise" lets
    them through — and for a team room that means posting whatever streamed
    before the failure, unmarked, in front of everyone."""
    from xyz_agent_context.agent_runtime._agent_runtime_steps.step_3_agent_loop import (  # noqa: E501
        _turn_hit_a_fatal,
    )
    from xyz_agent_context.schema import ErrorMessage

    frame = ErrorMessage(
        error_message="provider key expired",
        error_type="auth_expired",
        severity="fatal",
    )

    assert _turn_hit_a_fatal(None, [frame]) is True


def test_a_recoverable_hiccup_is_not_a_failed_turn():
    """The loop absorbed it and went on to answer. Calling this a failure both
    discards the real reply and announces a breakdown that did not happen."""
    from xyz_agent_context.agent_runtime._agent_runtime_steps.step_3_agent_loop import (  # noqa: E501
        _turn_hit_a_fatal,
    )
    from xyz_agent_context.schema import ErrorMessage

    frame = ErrorMessage(
        error_message="429 from provider, retried",
        error_type="rate_limit",
        severity="recoverable",
    )

    assert _turn_hit_a_fatal(None, [frame]) is False


def test_a_raised_failure_still_counts():
    from xyz_agent_context.agent_runtime._agent_runtime_steps.step_3_agent_loop import (  # noqa: E501
        _turn_hit_a_fatal,
    )

    assert _turn_hit_a_fatal({"error_type": "TimeoutError"}, []) is True


def test_a_recoverable_error_does_not_make_the_run_look_failed():
    """The collector's side of the same fact.

    `is_error` is set by any error frame; `is_fatal` is what a consumer deciding
    "did this turn produce usable output" has to read. Conflating them is what
    put a failure notice next to a correct answer.
    """
    from xyz_agent_context.agent_runtime.run_collector import RunCollection, RunError

    recoverable = RunCollection(
        output_text="here is your answer",
        tool_calls=[], raw_items=[],
        error=RunError("rate_limit", "429, retried", severity="recoverable"),
    )

    assert recoverable.is_error is True
    assert recoverable.is_fatal is False


def test_an_unlabelled_error_is_treated_as_fatal():
    """The safe direction: presenting a possibly-empty turn as a success is the
    more harmful mistake."""
    from xyz_agent_context.agent_runtime.run_collector import RunCollection, RunError

    unlabelled = RunCollection(
        output_text="", tool_calls=[], raw_items=[],
        error=RunError("SomeCrash", "boom"),
    )

    assert unlabelled.is_fatal is True


# ── the wiring, not just the parts ──────────────────────────────────────────
#
# The gate and the poster were each covered on their own, and that missed the
# thing most likely to break: how they are joined. Replacing the gate's verdict
# with `team_deliver is not None`, or passing `captured_error=None` by accident,
# left every test above green. These drive the phase as a whole and re-compute
# nothing.

def _ctx(*, deliverer, cancelled=False, channel_id="ch_1"):
    from types import SimpleNamespace

    return SimpleNamespace(
        on_plain_text_delivery=deliverer,
        cancellation=SimpleNamespace(is_cancelled=cancelled),
        trigger_extra_data={"bus_channel_id": channel_id},
    )


async def _run_phase(ctx, *, final_output, captured_error=None, response=None):
    from xyz_agent_context.agent_runtime._agent_runtime_steps.step_3_agent_loop import (  # noqa: E501
        _team_room_delivery_phase,
    )

    trace = [] if response is None else response
    frame = await _team_room_delivery_phase(
        ctx=ctx, final_output=final_output,
        agent_loop_response=trace, captured_error=captured_error,
    )
    return ([] if frame is None else [frame]), trace


@pytest.mark.asyncio
async def test_the_phase_posts_and_records_a_normal_team_turn():
    posted: list[str] = []

    async def _deliver(text: str) -> bool:
        posted.append(text)
        return True

    frames, trace = await _run_phase(
        _ctx(deliverer=_deliver), final_output="the OCR is done"
    )

    assert posted == ["the OCR is done"]
    # Yielded AND appended: downstream hooks read the list, not the stream.
    assert len(frames) == 1 and trace == frames


@pytest.mark.asyncio
async def test_the_phase_does_not_post_a_stopped_turn():
    """Through the real wiring. The cost of getting this wrong is not a stray
    line — the post path parses @mentions, so a turn the user killed could wake
    teammates into full runs of their own."""
    called = False

    async def _deliver(text: str) -> bool:
        nonlocal called
        called = True
        return True

    frames, trace = await _run_phase(
        _ctx(deliverer=_deliver, cancelled=True), final_output="half a sen"
    )

    assert called is False
    assert frames == [] and trace == []


@pytest.mark.asyncio
async def test_the_phase_does_not_post_after_a_raised_failure():
    called = False

    async def _deliver(text: str) -> bool:
        nonlocal called
        called = True
        return True

    frames, _ = await _run_phase(
        _ctx(deliverer=_deliver), final_output="half a sen",
        captured_error={"error_type": "TimeoutError"},
    )

    assert called is False
    assert frames == []


@pytest.mark.asyncio
async def test_the_phase_does_not_post_after_a_returned_fatal():
    """The path `captured_error` alone cannot see: fatal by severity, never
    raised, so the loop finished and `final_output` holds a fragment."""
    from xyz_agent_context.schema import ErrorMessage

    called = False

    async def _deliver(text: str) -> bool:
        nonlocal called
        called = True
        return True

    frames, _ = await _run_phase(
        _ctx(deliverer=_deliver), final_output="half a sen",
        response=[ErrorMessage(
            error_message="key expired", error_type="auth_expired",
            severity="fatal",
        )],
    )

    assert called is False
    assert frames == []


@pytest.mark.asyncio
async def test_the_phase_is_inert_outside_a_team_room():
    """Every chat / job / IM turn goes through here."""
    frames, trace = await _run_phase(
        _ctx(deliverer=None), final_output="a chat reply"
    )

    assert frames == [] and trace == []


@pytest.mark.asyncio
async def test_a_refused_post_records_nothing():
    async def _deliver(text: str) -> bool:
        return False

    frames, trace = await _run_phase(
        _ctx(deliverer=_deliver), final_output="never landed"
    )

    assert frames == [] and trace == []


def test_a_recovered_turn_is_not_fatal():
    """`recovered` means a fatal-class failure was papered over by the
    helper-LLM fallback, which produced a real reply. Calling it fatal throws
    that reply away and puts a failure notice in its place — the user is
    entitled to the answer that was produced for them."""
    from xyz_agent_context.agent_runtime.run_collector import RunCollection, RunError

    c = RunCollection(
        output_text="the fallback answer", tool_calls=[], raw_items=[],
        error=RunError("api_error", "sdk crashed", severity="recovered"),
    )

    assert c.is_error is True
    assert c.is_fatal is False


def test_a_turn_that_already_spoke_is_not_fatal():
    """`recovered_after_reply`: the agent replied, THEN something broke. The
    reply happened; the badge is for the unfinished remainder."""
    from xyz_agent_context.agent_runtime.run_collector import RunCollection, RunError

    c = RunCollection(
        output_text="here you go", tool_calls=[], raw_items=[],
        error=RunError("api_error", "died after replying",
                       severity="recovered_after_reply"),
    )

    assert c.is_fatal is False


def test_only_fatal_and_unlabelled_count_as_fatal():
    from xyz_agent_context.agent_runtime.run_collector import RunCollection, RunError

    for sev, expected in [
        ("fatal", True), ("", True),
        ("recoverable", False), ("recovered", False),
        ("recovered_after_reply", False),
    ]:
        c = RunCollection(
            output_text="", tool_calls=[], raw_items=[],
            error=RunError("e", "m", severity=sev),
        )
        assert c.is_fatal is expected, f"severity={sev!r}"
