"""
@file_name: test_turn_profile.py
@date: 2026-08-06
@description: TurnProfile — the per-turn fast-mode knob bundle.

Locks:
- Every default preserves today's pipeline behavior (profile absent and
  profile with defaults must be indistinguishable to consumers).
- voice_fast() factory carries the v1 decisions: FULL prompt, low
  reasoning effort, nexus_power override, speak as reply tool.
- Frozen (immutable) and JSON round-trippable (rides kwargs + wire).
"""
from __future__ import annotations

import pytest

from xyz_agent_context.schema.turn_profile import TurnProfile


def test_defaults_mean_no_behavior_change():
    p = TurnProfile()
    assert p.name == "default"
    assert p.narrative_strategy == "full"
    assert p.framework_override is None
    assert p.prompt_mode == "full"
    assert p.reasoning_effort is None
    assert p.include_arg_deltas is None
    assert p.is_fast is False


def test_voice_fast_factory_carries_v1_decisions():
    p = TurnProfile.voice_fast()
    assert p.name == "voice_fast"
    assert p.narrative_strategy == "bm25_top1"
    assert p.framework_override == "nexus_power"
    assert p.prompt_mode == "full"
    assert p.reasoning_effort == "low"
    assert p.include_arg_deltas is True
    assert p.is_fast is True


def test_frozen():
    p = TurnProfile()
    with pytest.raises(Exception):
        p.name = "x"  # type: ignore[misc]


def test_json_round_trip():
    p = TurnProfile.voice_fast(reasoning_effort="minimal")
    assert TurnProfile(**p.model_dump()) == p


def test_invalid_prompt_mode_rejected():
    with pytest.raises(Exception):
        TurnProfile(prompt_mode="tiny")  # type: ignore[arg-type]


def test_fast_for_derives_name_from_working_source_enum():
    from xyz_agent_context.schema.hook_schema import WorkingSource

    p = TurnProfile.fast_for(WorkingSource.CHAT)
    assert p.name == "chat_fast"
    assert p.narrative_strategy == "bm25_top1"
    assert p.framework_override == "nexus_power"
    assert p.prompt_mode == "full"
    assert p.reasoning_effort == "low"
    assert p.include_arg_deltas is True
    assert p.expression_nudge is True
    assert p.is_fast is True


def test_fast_for_accepts_bare_string_source():
    assert TurnProfile.fast_for("chat").name == "chat_fast"


def test_voice_fast_is_fast_for_voice():
    assert TurnProfile.voice_fast() == TurnProfile.fast_for("voice")
    assert TurnProfile.voice_fast(reasoning_effort="minimal") == TurnProfile.fast_for(
        "voice", reasoning_effort="minimal"
    )
