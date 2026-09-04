"""
@file_name: profiles.py
@author: Bin Liang
@date: 2026-09-04
@description: The five builtin ``PipelineProfile``s — fast/voice/job/silent as named strategy selections instead of scattered flags.

Each profile names, per stage, the strategy registered in the
``turn.pipeline.<stage>`` registry; a stage not named uses ``default``.
``TurnProfile`` (schema) keeps carrying the per-turn knobs the Act stage
still reads (reasoning effort, prompt mode, framework override); the
profile decides WHICH strategy runs, the knobs tune HOW.
"""
from __future__ import annotations

from narranexus.contracts.agent.pipeline import PipelineProfile
from narranexus.contracts.agent.stages import Stage
from narranexus.kernel.plugins.registry import Contribution

DEFAULT = PipelineProfile(id="default")
FAST = PipelineProfile(id="fast", strategies={Stage.RECALL: "narrative_fast"}, narrative_persistence="durable")
VOICE = PipelineProfile(id="voice", strategies={Stage.RECALL: "ephemeral"}, narrative_persistence="ephemeral")
JOB = PipelineProfile(id="job")
SILENT = PipelineProfile(id="silent", strategies={Stage.ACT: "silent"})

BUILTIN_PROFILES: dict[str, PipelineProfile] = {p.id: p for p in (DEFAULT, FAST, VOICE, JOB, SILENT)}

PROFILE_CONTRIBUTIONS = tuple(Contribution(pid, (lambda p=p: p)) for pid, p in BUILTIN_PROFILES.items())

__all__ = ["BUILTIN_PROFILES", "DEFAULT", "FAST", "JOB", "PROFILE_CONTRIBUTIONS", "SILENT", "VOICE"]
