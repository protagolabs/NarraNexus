"""
@file_name: turn_profile.py
@date: 2026-08-06
@description: Per-turn execution profile (fast mode). None everywhere = today's behavior.

The profile rides the existing pure-kwargs chain (run_stream ->
AgentRuntime.run -> RunContext -> TurnInput -> driver kwargs -> executor
wire) as one frozen value object. Every consumer must treat an absent
profile — and a profile left at defaults — as "behave exactly as today";
fast mode is strictly additive.
"""
from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel


class TurnProfile(BaseModel, frozen=True):
    """Per-turn knobs for fast mode.

    Every field's default preserves the current pipeline behavior; a
    consumer receiving ``profile=None`` must not change anything.
    JSON-serializable (``model_dump()`` crosses the executor wire).
    """

    name: str = "default"
    narrative_strategy: Literal["full", "bm25_top1"] = "full"
    framework_override: Optional[str] = None          # e.g. "nexus_power"
    prompt_mode: Literal["full", "minimal"] = "full"
    reasoning_effort: Optional[str] = None            # -> llm_extra["reasoning_effort"]
    include_arg_deltas: Optional[bool] = None         # None = TurnOptions default
    expression_nudge: Optional[bool] = None           # None = TurnOptions default
    # NOTE deliberately absent: a reply_tool field. The reply surface is
    # declared by modules (get_expressive_tools orders speak first on
    # voice turns via extra_data) — a profile field nothing consumes
    # would be exactly the declared-but-unimplemented schema trap
    # turn_input.py warns about.

    @classmethod
    def voice_fast(cls, *, reasoning_effort: str = "low") -> "TurnProfile":
        """The F28 voice-call profile (v1 decisions: FULL prompt, tools kept)."""
        return cls(
            name="voice_fast",
            narrative_strategy="bm25_top1",
            framework_override="nexus_power",
            prompt_mode="full",
            reasoning_effort=reasoning_effort,
            include_arg_deltas=True,
            # An unanswered voice turn is never the right outcome — a
            # mute turn gets one steering nudge before it may close.
            expression_nudge=True,
        )

    @property
    def is_fast(self) -> bool:
        return self.name != "default"
