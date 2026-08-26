"""
@file_name: test_im_dm_fallback_loop_guard.py
@author:
@date: 2026-08-24
@description: The DM fallback must not become a ping-pong engine.

``no_reply_im_dm`` writes a reply the agent never sent and delivers it on
the channel. Until now it asked exactly one question — "was a reply tool
called?" — and would happily invent a reply to another agent, forever.

``message_bus`` was excluded from the fallback from the start, with the
reasoning "must not answer peer agents". But agent-to-agent conversations
also arrive over IM channels: the 8/14 incident was two agents in a
NarraMessenger DM, which is precisely the path the exclusion did not
cover. These tests pin the two new gates.
"""
from __future__ import annotations

import pytest

from xyz_agent_context.agent_runtime._agent_runtime_steps.step_3_agent_loop import (
    IM_DM_FALLBACK_BURST_LIMIT,
    _fallback_conversation_key,
    _record_fallback_delivery,
    _recent_fallback_count,
    _should_run_helper_llm_fallback,
    reset_im_dm_fallback_history,
)
from xyz_agent_context.agent_runtime._agent_runtime_steps.step_3_agent_loop import (
    SKIP_REASON_AGENT_PEER,
    SKIP_REASON_FALLBACK_RATE_LIMITED,
)
from xyz_agent_context.schema import ErrorMessage, ProgressMessage, ProgressStatus


def _step3():
    """The MODULE — ``_agent_runtime_steps/__init__`` re-exports a function
    of the same name, so a plain ``from ... import step_3_agent_loop`` hands
    back the function."""
    import importlib

    return importlib.import_module(
        "xyz_agent_context.agent_runtime._agent_runtime_steps.step_3_agent_loop"
    )


def _idle_progress() -> ProgressMessage:
    """A tool call that is NOT a reply — the shape that arms the fallback."""
    return ProgressMessage(
        step="3.4.1",
        title="Tool call",
        description="get_chat_history",
        status=ProgressStatus.COMPLETED,
        details={"tool_name": "mcp__chat_module__get_chat_history"},
    )


def _decide(**overrides):
    kwargs = dict(
        working_source="narramessenger",
        agent_loop_response=[_idle_progress()],
        cancellation=None,
        is_direct_message=True,
    )
    kwargs.update(overrides)
    return _should_run_helper_llm_fallback(**kwargs)


# ── The baseline this PR must not break ───────────────────────────────

def test_human_dm_still_gets_its_fallback():
    mode, reason = _decide()
    assert mode == "no_reply_im_dm"
    assert reason == ""


# ── Gate 1: never invent a reply to another agent ─────────────────────

def test_agent_peer_dm_gets_no_invented_reply():
    mode, reason = _decide(is_agent_peer=True)
    assert mode is None
    assert reason == "agent_peer_no_fallback"


def test_agent_peer_gate_does_not_leak_into_group_rooms():
    """Group rooms were already silent for a different reason; the more
    specific existing reason must survive."""
    mode, reason = _decide(is_direct_message=False, is_agent_peer=True)
    assert mode is None
    assert reason == "group_room_may_stay_silent"


def test_organic_reply_still_reported_as_such_for_an_agent_peer():
    """Reason precedence: what actually happened beats what we'd have
    refused to do anyway."""
    reply = ProgressMessage(
        step="3.4.1",
        title="Tool call",
        description="narra_reply",
        status=ProgressStatus.COMPLETED,
        details={"tool_name": "mcp__narramessenger_module__narra_reply",
                 "arguments": {"content": "hi"}},
    )
    mode, reason = _decide(agent_loop_response=[reply], is_agent_peer=True)
    assert mode is None
    assert reason == "already_replied_via_tool"


def test_fatal_error_reason_beats_agent_peer_reason():
    fatal = ErrorMessage(
        error_message="boom", error_type="RuntimeError", severity="fatal"
    )
    mode, reason = _decide(
        agent_loop_response=[_idle_progress(), fatal], is_agent_peer=True
    )
    assert mode is None
    assert reason == "fatal_no_invented_reply"


# ── Gate 2: a steady stream of fallbacks is a loop being fed ──────────

def test_fallback_arms_below_the_burst_limit():
    mode, _ = _decide(recent_fallback_count=IM_DM_FALLBACK_BURST_LIMIT - 1)
    assert mode == "no_reply_im_dm"


def test_fallback_stops_at_the_burst_limit():
    mode, reason = _decide(recent_fallback_count=IM_DM_FALLBACK_BURST_LIMIT)
    assert mode is None
    assert reason == "fallback_rate_limited"


# ── The counter itself ────────────────────────────────────────────────

def test_counter_is_scoped_per_conversation():
    a = _fallback_conversation_key({"channel": "narramessenger", "room_id": "!a"}, "agt_1")
    b = _fallback_conversation_key({"channel": "narramessenger", "room_id": "!b"}, "agt_1")
    assert a != b

    _record_fallback_delivery(a)
    _record_fallback_delivery(a)
    assert _recent_fallback_count(a) == 2
    assert _recent_fallback_count(b) == 0, "one noisy room must not gag another"


def test_counter_ignores_an_unidentifiable_conversation():
    """No room → no key.

    (The predicate used to be "no channel AND no room". ``channel`` is
    always set — it is the trigger's own name — so that made the empty-room
    case fall through to a per-CHANNEL bucket where unrelated DMs would
    starve each other's budget, which is the opposite of what this carve-out
    is for.)"""
    key = _fallback_conversation_key({}, "agt_1")
    assert key == ""
    _record_fallback_delivery(key)
    assert _recent_fallback_count(key) == 0


def test_same_room_on_two_channels_is_two_conversations():
    a = _fallback_conversation_key({"channel": "telegram", "room_id": "123"}, "agt_1")
    b = _fallback_conversation_key({"channel": "slack", "room_id": "123"}, "agt_1")
    assert a != b


def test_two_agents_in_one_room_get_separate_budgets():
    """The key is agent-scoped. Today the gate only fires on DMs (one of
    our agents alone in the room), so this cannot bite yet — but the map
    is module-level and shared by every agent in the runtime process, so
    the moment the gate widens beyond DMs an agent-blind key would let
    agent A's three fallbacks silently gag agent B."""
    a = _fallback_conversation_key({"channel": "slack", "room_id": "C1"}, "agt_a")
    b = _fallback_conversation_key({"channel": "slack", "room_id": "C1"}, "agt_b")
    assert a != b

    _record_fallback_delivery(a)
    _record_fallback_delivery(a)
    _record_fallback_delivery(a)
    assert _recent_fallback_count(b) == 0


# ── Memory ────────────────────────────────────────────────────────────

def _step3_module():
    """The MODULE, not the same-named function.

    ``_agent_runtime_steps/__init__`` re-exports a ``step_3_agent_loop``
    function, so ``from ... import step_3_agent_loop`` hands back the
    function and every module-attribute access fails with a confusing
    "'function' object has no attribute ...".
    """
    import importlib

    return importlib.import_module(
        "xyz_agent_context.agent_runtime._agent_runtime_steps.step_3_agent_loop"
    )


def test_history_does_not_grow_without_bound():
    """`_recent_fallback_count` only cleans the key it is asked about, so
    a room that gets one fallback and never another would keep its entry
    for the life of the process."""
    step3 = _step3_module()

    reset_im_dm_fallback_history()
    for i in range(500):
        _record_fallback_delivery(f"telegram:room{i}")

    # Everything is inside the window, so nothing is droppable yet.
    assert len(step3._im_dm_fallback_history) == 500

    # Age them all out, then record one more.
    for stamps in step3._im_dm_fallback_history.values():
        stamps[:] = [t - step3.IM_DM_FALLBACK_WINDOW_SECONDS - 1 for t in stamps]
    _record_fallback_delivery("telegram:fresh")

    assert len(step3._im_dm_fallback_history) == 1
    assert "telegram:fresh" in step3._im_dm_fallback_history
    reset_im_dm_fallback_history()


def test_pruning_keeps_a_conversation_that_is_still_inside_its_window():
    step3 = _step3_module()

    reset_im_dm_fallback_history()
    _record_fallback_delivery("telegram:live")
    _record_fallback_delivery("telegram:other")
    assert _recent_fallback_count("telegram:live") == 1
    assert len(step3._im_dm_fallback_history) == 2
    reset_im_dm_fallback_history()


# ─────────────────────────────────────────────────────────────────────
# The chain this PR closes
#
# #359 added a "Breaking a Loop" section telling the agent it may stay
# silent, and left a warning in the source saying it did not take effect:
# staying silent means not calling the reply tool, which is precisely what
# `no_reply_im_dm` treats as "forgot to reply" — so the platform wrote one
# anyway. #362 gave the platform the `is_agent_peer` signal. This PR is the
# gate that turns chosen silence into actual silence.
#
# Pinned end-to-end because each half is individually green while the chain
# is broken: the prompt says one thing, the runtime does another, and
# nothing fails.
# ─────────────────────────────────────────────────────────────────────


def test_the_prompt_permits_silence_and_the_runtime_now_honours_it():
    """Prompt half and runtime half must agree for an agent peer."""
    from xyz_agent_context.channel.channel_prompts import (
        COMMUNICATION_PROTOCOL_DIRECT,
    )
    from xyz_agent_context.schema.channel_tag import AGENT_PEER_MARKER

    # The prompt tells the model it may stay silent toward a machine...
    assert "### Breaking a Loop" in COMMUNICATION_PROTOCOL_DIRECT
    assert AGENT_PEER_MARKER in COMMUNICATION_PROTOCOL_DIRECT

    # ...and the runtime no longer overrides that choice.
    mode, reason = _decide(is_agent_peer=True)
    assert mode is None
    assert reason == "agent_peer_no_fallback"


def test_a_human_dm_keeps_the_0802_fix():
    """The other half of the contract, and the reason this gate is narrow.

    ``no_reply_im_dm`` IS the 0802 fix (a person sent "hello" and got
    nothing). Suppressing it for humans to make the loop-breaker "work
    everywhere" would reopen that. A human DM still gets a written reply —
    the burst limit bounds a loop instead of stopping it dead.
    """
    mode, _ = _decide(is_agent_peer=False)
    assert mode == "no_reply_im_dm"

    for n in range(IM_DM_FALLBACK_BURST_LIMIT):
        assert _decide(recent_fallback_count=n)[0] == "no_reply_im_dm"
    assert _decide(recent_fallback_count=IM_DM_FALLBACK_BURST_LIMIT)[0] is None


def test_the_source_no_longer_claims_the_section_is_inert():
    """#359 left `THIS SECTION DOES NOT TAKE EFFECT YET` with an explicit
    instruction to delete it in the commit that adds the gate. A stale
    warning is as misleading as a stale promise."""
    import inspect

    from xyz_agent_context.channel import channel_prompts

    from xyz_agent_context.agent_runtime._agent_runtime_steps.step_3_agent_loop import (
        SKIP_REASON_AGENT_PEER,
        SKIP_REASON_FALLBACK_RATE_LIMITED,
    )

    src = inspect.getsource(channel_prompts)
    assert "DOES NOT TAKE EFFECT YET" not in src
    # Reference the CONSTANTS, not copies of their values: asserting the
    # literals would stay green after a rename, because the stale comment
    # still contains the old word — which is the drift this claims to catch.
    assert SKIP_REASON_AGENT_PEER in src
    assert SKIP_REASON_FALLBACK_RATE_LIMITED in src


# ── The envelope → decision hop ───────────────────────────────────────
#
# Every test above calls `_should_run_helper_llm_fallback` with
# `is_agent_peer=True` directly, so none of them checks that the key name
# on the wire matches the one read here. This file's own comments record
# TWO incidents that died exactly on this hop — `ctx` passed where
# `context` was meant (the whole IM DM fallback was dead code), and the
# envelope added to one of four construction sites. Both were all-green.


def test_the_flag_survives_the_envelope_hop():
    from types import SimpleNamespace

    from xyz_agent_context.agent_runtime._agent_runtime_steps.step_3_agent_loop import (
        _channel_turn_envelope,
    )
    from xyz_agent_context.channel.channel_prompts import ROOM_TYPE_DIRECT
    from xyz_agent_context.schema.channel_tag import ChannelTag

    # Built through ChannelTag.to_dict() on purpose — hand-writing the dict
    # would let a key rename drift both sides together and pin nothing.
    tag = ChannelTag(
        channel="narramessenger", sender_name="Liam", sender_id="@agent-x:h",
        room_id="!r", is_agent_peer=True,
    )
    context = SimpleNamespace(
        ctx_data=SimpleNamespace(
            extra_data={
                "channel_tag": tag.to_dict(),
                "channel_room_type": ROOM_TYPE_DIRECT,
            }
        )
    )

    envelope = _channel_turn_envelope(context)
    mode, reason = _should_run_helper_llm_fallback(
        working_source="narramessenger",
        agent_loop_response=[_idle_progress()],
        cancellation=None,
        is_direct_message=(
            envelope.get("channel_room_type") == ROOM_TYPE_DIRECT
        ),
        is_agent_peer=bool(
            (envelope.get("channel_tag") or {}).get("is_agent_peer", False)
        ),
    )
    assert mode is None
    assert reason == "agent_peer_no_fallback"


def test_a_conversation_without_a_room_is_not_counted():
    """`channel` is always set (it is the trigger's own name), so the
    empty-key carve-out hinges on `room_id` alone. Without this, an empty
    room falls into a per-CHANNEL bucket where unrelated DMs starve each
    other's budget."""
    assert _fallback_conversation_key({"channel": "telegram"}, "agt_1") == ""
    assert _fallback_conversation_key({}, "agt_1") == ""
    assert _fallback_conversation_key(
        {"channel": "telegram", "room_id": "r1"}, "agt_1"
    ) == "agt_1:telegram:r1"


def test_the_key_is_normalised_inside_the_function():
    """Decision side and record side compute this key independently; if one
    normalised `agent_id` and the other did not, they would address two
    buckets that never meet and the gate would silently never fire."""
    assert _fallback_conversation_key({"channel": "c", "room_id": "r"}, None) == \
        _fallback_conversation_key({"channel": "c", "room_id": "r"}, "")


# ── The audit row ─────────────────────────────────────────────────────
#
# `_audit_fallback_suppressed` is the only reason this gate is diagnosable
# after the fact: `fallback_rate_limited` fires off an in-process window a
# restart wipes, and the person on the other end just sees the assistant
# stop answering. If the write never happens, "the audit never ran" and
# "the audit ran and the table is empty" look identical afterwards — the
# failure is invisible precisely when it is needed.


async def test_a_suppressed_turn_writes_one_audit_row(monkeypatch):
    rows = []

    class _Auditor:
        def __init__(self, service):
            self.service = service

        async def event(self, event_type, detail=None):
            rows.append((self.service, event_type, detail))

    # Patch the CLASS the module imports lazily, and clear the cached
    # instance so the patched one is used. Letting the real ServiceAuditor
    # run would `await get_db_client()`, fail, and get swallowed by the
    # never-raise handler — the assertion would pass having tested nothing.
    import xyz_agent_context.services.service_audit as audit_mod

    monkeypatch.setattr(audit_mod, "ServiceAuditor", _Auditor)
    monkeypatch.setattr(_step3(), "_fallback_auditor", None)

    await _step3()._audit_fallback_suppressed(
        reason=SKIP_REASON_AGENT_PEER,
        agent_id="agt_1",
        channel_tag={"channel": "narramessenger", "room_id": "!r",
                     "is_agent_peer": True},
        window_count=7,
        conversation_key="agt_1:narramessenger:!r",
    )

    assert len(rows) == 1
    service, event_type, detail = rows[0]
    assert service == _step3()._FALLBACK_AUDIT_SERVICE
    assert event_type == SKIP_REASON_AGENT_PEER
    assert {"agent_id", "channel", "room_id", "is_agent_peer", "window_count"} <= set(detail)
    assert detail["window_count"] == 7
    assert detail["room_id"] == "!r"


async def test_the_audit_is_cooled_per_conversation_and_reason(monkeypatch):
    """The write rate is set by the far side, not by us."""
    rows = []

    class _Auditor:
        def __init__(self, service):
            pass

        async def event(self, event_type, detail=None):
            rows.append((event_type, detail))

    import xyz_agent_context.services.service_audit as audit_mod

    monkeypatch.setattr(audit_mod, "ServiceAuditor", _Auditor)
    monkeypatch.setattr(_step3(), "_fallback_auditor", None)

    async def _fire(reason, key):
        await _step3()._audit_fallback_suppressed(
            reason=reason, agent_id="a", channel_tag={"channel": "c", "room_id": "r"},
            window_count=1, conversation_key=key,
        )

    for _ in range(5):
        await _fire(SKIP_REASON_AGENT_PEER, "a:c:r1")
    assert len(rows) == 1, "a fed conversation must not write a row per turn"

    await _fire(SKIP_REASON_AGENT_PEER, "a:c:r2")
    assert len(rows) == 2, "a different conversation has its own slot"

    await _fire(SKIP_REASON_FALLBACK_RATE_LIMITED, "a:c:r1")
    assert len(rows) == 3, (
        "the two gates are different facts — one must not eat the other's slot"
    )


async def test_a_failed_audit_write_does_not_arm_the_cooldown(monkeypatch):
    """A transient DB blip must not silence this conversation for the rest
    of the window."""
    calls = []

    class _BoomThenOk:
        def __init__(self, service):
            pass

        async def event(self, event_type, detail=None):
            calls.append(event_type)
            if len(calls) == 1:
                raise RuntimeError("db down")

    import xyz_agent_context.services.service_audit as audit_mod

    monkeypatch.setattr(audit_mod, "ServiceAuditor", _BoomThenOk)
    monkeypatch.setattr(_step3(), "_fallback_auditor", None)

    for _ in range(2):
        await _step3()._audit_fallback_suppressed(
            reason=SKIP_REASON_AGENT_PEER, agent_id="a",
            channel_tag={"channel": "c", "room_id": "r"},
            window_count=1, conversation_key="a:c:r",
        )
    assert len(calls) == 2, "the first (failed) write must not arm the cooldown"


def test_only_the_two_new_reasons_are_audited():
    """The other three skips are recomputable from the turn itself, so
    auditing them would be noise. Pinned because "let's just audit all
    five" is an easy and wrong cleanup."""
    import inspect

    src = inspect.getsource(_step3().step_3_agent_loop)
    marker = "if skip_reason in ("
    assert marker in src
    window = src[src.index(marker): src.index(marker) + 200]
    assert "SKIP_REASON_AGENT_PEER" in window
    assert "SKIP_REASON_FALLBACK_RATE_LIMITED" in window
    assert "already_replied_via_tool" not in window
