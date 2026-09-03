"""
@file_name: model_identity.py
@author:
@date: 2026-07-10
@description: Resolve an agent's REAL runtime identity (coding-agent
framework + model) for display in the system prompt.

Why this exists
---------------
The "LLM Model" line in BasicInfoModule's system prompt used to be a
hardcoded literal ("Claude Agent SDK" / "sonnet-4") in
``context_runtime.py``, so every agent — regardless of its actual
configuration — told the user it was Claude Sonnet-4. This module
resolves the truth from the same slot rows the runtime dispatches on,
so the prompt states what the agent actually runs (e.g. "Codex CLI"
/ "gpt-5").

Iron rule #9: this lives in the agent_framework layer, not inside a
Module. BasicInfoModule (a Module) just calls ``resolve_agent_model_
identity`` and renders the strings — it never learns framework names.

This is the SINGLE overlay implementation. The dispatch-side resolver
``agent_runtime._agent_runtime_steps.step_3_agent_loop.
_resolve_agent_framework_name`` delegates here (returns ``.framework``),
so the identity shown in the prompt can never disagree with the driver
that actually runs. The rule: a per-agent ``agent_slots`` override wins
ONLY when it truly rebinds the slot — it carries BOTH a ``provider_id``
AND an ``agent_framework`` (a provider-only or framework-only stub does
NOT win, matching the config resolver, which would otherwise e.g. run
the Codex driver against a Claude config). Otherwise the owner's
``user_slots`` row (keyed by ``agents.created_by``) is authoritative.
Framework and model are read from that SAME winning slot row.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from loguru import logger

# Canonical framework name → how the agent names its own runtime INSIDE the
# system prompt. This is prompt copy, deliberately NOT the UI label: the
# frontend's directory / profile labels live in `lib/frameworkBrand.ts` and
# read differently ("Claude Code" vs the SDK name here). Changing a value
# below changes what the agent says about itself — treat it as a prompt
# edit. Unknown names fall back to the raw canonical string (never invent a
# brand).
FRAMEWORK_DISPLAY_NAMES: dict[str, str] = {
    "codex_cli": "Codex CLI",
    "claude_code": "Claude Agent SDK",
    "nexus_power": "NexusPower-beta",
}

# Platform default since 2026-08-20 (#336). THE constant: user_service's
# owner-level read, slot_service's directory projection and the identity
# overlay below all import it, so changing the default is one edit.
DEFAULT_AGENT_FRAMEWORK = "nexus_power"


@dataclass(frozen=True)
class AgentModelIdentity:
    """The agent's runtime identity for prompt display.

    - ``framework``: canonical framework name (e.g. ``"codex_cli"``).
    - ``framework_display``: human label (e.g. ``"Codex CLI"``).
    - ``model``: the configured model string on the agent slot
      (e.g. ``"gpt-5"``); may be empty when the slot lets the CLI pick
      its own default.
    """

    framework: str
    framework_display: str
    model: str


def _display_for(framework: str) -> str:
    return FRAMEWORK_DISPLAY_NAMES.get(framework, framework)


def slot_rebinds(override: dict | None) -> bool:
    """Whether an ``agent_slots`` row rebinds the agent slot for identity.

    True only when the row carries BOTH a ``provider_id`` AND an
    ``agent_framework``. A provider-only or framework-only stub does not win:
    the config resolver skips empty-provider rows, and honouring a
    framework-only stub here would run e.g. the Codex driver against a Claude
    config.
    """
    return bool(override and override.get("provider_id") and override.get("agent_framework"))


def effective_agent_slot(
    override: dict | None, owner_default: dict | None
) -> dict | None:
    """The slot row THIS agent actually runs on — the one place that rule lives.

    Pure, so every projection of "what does this agent run" (the system
    prompt, the driver dispatch, the agents directory) can share it instead of
    re-deriving it against a hand-written query and drifting — which is how the
    directory once showed a framework-only stub's brand for an agent that was
    running on the owner default.
    """
    if slot_rebinds(override):
        return override
    return owner_default


def framework_of(slot: dict | None) -> str:
    """Framework name a slot row resolves to; the platform default when the
    row is missing or the column is null."""
    return (slot or {}).get("agent_framework") or DEFAULT_AGENT_FRAMEWORK


async def resolve_agent_model_identity(
    agent_id: str, db: Any
) -> AgentModelIdentity:
    """Resolve THIS agent's real (framework, model) for prompt display.

    Overlay (the authority ``_resolve_agent_framework_name`` delegates to):
      1. Per-agent override — ``agent_slots[agent_id, 'agent']`` wins
         ONLY when it carries BOTH a ``provider_id`` AND an
         ``agent_framework`` (a provider-only or framework-only stub does
         not rebind the slot; the config resolver skips it too).
      2. Owner default — ``user_slots[owner, 'agent']`` where
         ``owner = agents.created_by``.

    Both framework and model come from whichever slot row wins, so the
    displayed identity matches what the driver actually runs.

    Never raises: any missing row / null column / DB error degrades to
    ``(DEFAULT_AGENT_FRAMEWORK, "")`` so identity resolution can never break
    the system-prompt build. The default framework is displayed via the
    same map, so the prompt still says something truthful-by-fallback
    rather than a wrong brand.
    """
    slot: dict | None = None
    try:
        override = await db.get_one(
            "agent_slots", {"agent_id": agent_id, "slot_name": "agent"}
        )
        owner_default: dict | None = None
        if not slot_rebinds(override):
            agent_row = await db.get_one("agents", {"agent_id": agent_id})
            owner = (agent_row or {}).get("created_by")
            if owner:
                owner_default = await db.get_one(
                    "user_slots", {"user_id": owner, "slot_name": "agent"}
                )
        slot = effective_agent_slot(override, owner_default)
    except Exception as e:  # noqa: BLE001 — defensive: any DB hiccup
        logger.warning(
            f"[agent_identity] slot lookup failed for agent={agent_id}: {e}; "
            f"falling back to {DEFAULT_AGENT_FRAMEWORK}"
        )
        slot = None

    framework = framework_of(slot)
    model = (slot or {}).get("model") or ""
    return AgentModelIdentity(
        framework=framework,
        framework_display=_display_for(framework),
        model=model,
    )
