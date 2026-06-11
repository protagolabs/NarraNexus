"""
@file_name: test_claude_reasoning_mapping.py
@author: Bin Liang
@date: 2026-06-10
@description: Claude adapter mapping for the framework-neutral reasoning
params (SlotConfig.thinking / SlotConfig.reasoning_effort).

The slot stores neutral values; this adapter owns the Claude dialect:

  thinking "on"  -> ClaudeAgentOptions.thinking = {"type": "adaptive"}
  thinking "off" -> ClaudeAgentOptions.thinking = {"type": "disabled"}
  thinking ""    -> ClaudeAgentOptions.thinking = {"type": "adaptive"}
                    (auto → adaptive; see incident 2026-06-11 below)
  effort low/medium/high/max -> ClaudeAgentOptions.effort (1:1, Claude
                                supports the full neutral vocabulary)
  effort ""      -> option absent

Incident 2026-06-11: auto used to leave the thinking key absent, letting
the bundled Claude Code CLI inject the legacy ``{"type": "enabled",
"budget_tokens": N}`` shape — which every current Claude model (Opus
4.6/4.7/4.8, Sonnet 4.6, Fable 5) rejects with a 400 ("thinking.type.enabled
is not supported for this model"). Auto now sends ``adaptive`` explicitly,
the universal on-mode for current models and the Anthropic-recommended
default. We never emit ``enabled``.

Out-of-vocabulary values cannot normally reach here (SlotConfig validates),
but the mapper must still degrade safely (adaptive for thinking, absent for
effort) with a warning rather than raise — a bad tuning knob must never take
the agent loop down (defensive only).
"""
from __future__ import annotations

from xyz_agent_context.agent_framework.api_config import ClaudeConfig
from xyz_agent_context.agent_framework.xyz_claude_agent_sdk import (
    _resolve_reasoning_options,
)


def test_auto_maps_to_adaptive():
    """REGRESSION (2026-06-11): auto must emit adaptive, NOT leave the key
    absent — an absent key let the CLI inject the rejected ``enabled`` shape
    and every turn on a current model 400'd."""
    assert _resolve_reasoning_options("", "") == {"thinking": {"type": "adaptive"}}


def test_thinking_on_maps_to_adaptive():
    assert _resolve_reasoning_options("on", "") == {"thinking": {"type": "adaptive"}}


def test_thinking_off_maps_to_disabled():
    assert _resolve_reasoning_options("off", "") == {"thinking": {"type": "disabled"}}


def test_effort_passes_through_with_adaptive_default():
    """effort is independent of thinking; with thinking auto, the result
    carries both the adaptive default and the effort level."""
    for level in ("low", "medium", "high", "max"):
        assert _resolve_reasoning_options("", level) == {
            "thinking": {"type": "adaptive"},
            "effort": level,
        }


def test_off_with_effort_keeps_disabled():
    assert _resolve_reasoning_options("off", "high") == {
        "thinking": {"type": "disabled"},
        "effort": "high",
    }


def test_combined_knobs_are_independent():
    assert _resolve_reasoning_options("on", "low") == {
        "thinking": {"type": "adaptive"},
        "effort": "low",
    }


def test_unknown_thinking_degrades_to_adaptive():
    """Defensive: an unknown thinking value defaults to adaptive (the safe
    on-mode) with a warning — never absent (which would resurrect the CLI's
    rejected ``enabled`` default) and never raised. Unknown effort is dropped."""
    assert _resolve_reasoning_options("weird", "xhigh") == {
        "thinking": {"type": "adaptive"},
    }


def test_claude_config_carries_neutral_params_with_auto_defaults():
    cfg = ClaudeConfig()
    assert cfg.thinking == ""
    assert cfg.reasoning_effort == ""
    cfg2 = ClaudeConfig(thinking="on", reasoning_effort="high")
    assert cfg2.thinking == "on"
    assert cfg2.reasoning_effort == "high"
