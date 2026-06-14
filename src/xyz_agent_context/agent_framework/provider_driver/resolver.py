"""
@file_name: resolver.py
@author: Bin Liang
@date: 2026-05-13
@description: Single-point Resolver — user_id → (ClaudeConfig,
              OpenAIConfig).

Replaces the two parallel paths that used to do this:

* HTTP request path: ``provider_resolver.ProviderResolver.resolve_and_set``
* Background trigger path: ``api_config._get_user_llm_configs_strict``

Both paths re-implemented "look up the 3 user_slots rows, fetch their
provider cards, build configs" with subtly different completeness
checks. This module folds them into one function. The legacy entry
points become thin shells that delegate here, so we get drop-in
compatibility while the call-site cleanup catches up.

Pipeline per call:

1. Load the user's quota row (still needed for the existing
   prefer_system_override branching during the migration window).
2. For each slot (agent / helper_llm):
   a. Look up the user_slots row.
   b. Look up the corresponding user_providers card by provider_id.
   c. Run self_heal_if_broken to auto-repair if slot.model ∉ card.models.
   d. Look up the Driver class via card.driver_type.
   e. Call the appropriate ``build_*_config`` on the Driver.
3. Return the two configs.

Visibility rule: a slot can point at a card owned by the user OR at
a system-shared card (owner_user_id IS NULL). Anything else is an
attempt to use someone else's credentials — fail with an explicit
error rather than silently building a config.
"""
from __future__ import annotations

import dataclasses
import json
from typing import TYPE_CHECKING

from loguru import logger

from xyz_agent_context.agent_framework.api_config import (
    CodexConfig,
    ClaudeConfig,
    LLMConfigNotConfigured,
    OpenAIConfig,
    RuntimeLLMConfigs,
)
from xyz_agent_context.agent_framework.provider_driver.base import (
    ProviderCard,
)
from xyz_agent_context.agent_framework.provider_driver.registry import (
    get_driver_class,
)
from xyz_agent_context.agent_framework.provider_driver.self_heal import (
    self_heal_if_broken,
)

if TYPE_CHECKING:
    from xyz_agent_context.utils.database import AsyncDatabaseClient


# Slot name → (build method name, config builder mapping). Single source
# of truth so we can iterate cleanly below.
_SLOT_BUILDERS = {
    "agent": "build_claude_config",
    "helper_llm": "build_openai_config",
}


# Coding-agent framework names this resolver knows. Must stay in sync
# with the entries registered in ``agent_framework/__init__.py``.
_KNOWN_AGENT_FRAMEWORKS = ("claude_code", "codex_cli")


def _agent_framework_from_slot(slot: dict | None) -> str:
    framework = (slot or {}).get("agent_framework") or "claude_code"
    if framework not in _KNOWN_AGENT_FRAMEWORKS:
        return "claude_code"
    return framework


def _is_codex_framework(framework: str | None) -> bool:
    """Codex framework needs a CodexConfig built from an OpenAI-protocol
    provider; non-codex frameworks (Claude Code) take ClaudeConfig
    instead. Kept as a helper rather than an inline equality check so a
    future v3 framework name lands in one spot."""
    return framework == "codex_cli"


def _slot_reasoning_params(slot: dict | None) -> tuple[str, str]:
    """Parse the framework-neutral (thinking, reasoning_effort) pair from
    a raw ``user_slots`` row's ``params_json``. Malformed / missing JSON
    degrades to ("", "") — auto — never raises (legacy rows predate the
    column)."""
    raw = (slot or {}).get("params_json")
    if not raw:
        return "", ""
    try:
        data = json.loads(raw) if isinstance(raw, str) else dict(raw)
    except (ValueError, TypeError):
        return "", ""
    if not isinstance(data, dict):
        return "", ""
    return str(data.get("thinking") or ""), str(data.get("reasoning_effort") or "")


def _codex_config_from_card(
    card: ProviderCard,
    model: str,
    thinking: str = "",
    reasoning_effort: str = "",
) -> CodexConfig:
    """Build the Codex runtime config from an OpenAI-protocol provider row."""
    if (card.protocol or "").lower() != "openai":
        raise NotImplementedError(
            f"Codex CLI requires an OpenAI-protocol agent provider, got "
            f"{card.protocol!r}."
        )

    auth_ref = card.auth_ref or ""
    if card.source == "codex_oauth" and (card.auth_type or "").lower() == "oauth":
        from xyz_agent_context.agent_framework.provider_driver.derive import (
            CODEX_CLI_CREDENTIALS_REF,
        )
        auth_ref = CODEX_CLI_CREDENTIALS_REF

    return CodexConfig(
        api_key=card.api_key,
        base_url=card.base_url,
        model=model,
        auth_type=card.auth_type or "api_key",
        auth_ref=auth_ref,
        thinking=thinking,
        reasoning_effort=reasoning_effort,
    )


def _is_visible(card: ProviderCard, user_id: str) -> bool:
    """Cards are visible if owned by this user, or system-shared
    (owner_user_id IS NULL, cloud only).

    Legacy rows from before Phase 0 backfill have ``owner_user_id``
    still null — for those we fall back to ``card.user_id`` matching.
    """
    if card.owner_user_id is None:
        # Two cases:
        #   1. System-shared card (cloud only) — accept regardless of user.
        #   2. Legacy row not yet backfilled — fall back to user_id match.
        if card.user_id and card.user_id != user_id:
            return False
        return True
    return card.owner_user_id == user_id


async def resolve_user_llm_configs(
    user_id: str,
    db: "AsyncDatabaseClient",
) -> tuple[ClaudeConfig, OpenAIConfig]:
    cfg = await resolve_user_runtime_llm_configs(user_id, db)
    return cfg.claude, cfg.openai


async def resolve_user_runtime_llm_configs(
    user_id: str,
    db: "AsyncDatabaseClient",
) -> RuntimeLLMConfigs:
    """Resolve a user's agent + helper_llm (+ optional Codex) configs in one shot.

    Raises ``LLMConfigNotConfigured`` if any required piece is missing
    or invisible. The caller is responsible for any further handling —
    e.g. AgentRuntime currently catches this and surfaces a friendly
    error message to the user.
    """
    # Pull all slot rows for this user.
    slot_rows = await db.get("user_slots", {"user_id": user_id})
    by_slot_name = {r.get("slot_name"): r for r in slot_rows or []}

    required = set(_SLOT_BUILDERS.keys())
    missing_slots = required - by_slot_name.keys()
    if missing_slots:
        raise LLMConfigNotConfigured(
            f"User {user_id!r} is missing the following slot bindings: "
            f"{sorted(missing_slots)}. Configure them in Settings → Providers."
        )

    # For each slot: card lookup → visibility check → self-heal → Driver
    # dispatch → build_*_config.
    cfgs: dict[str, object] = {}
    for slot_name, builder_method in _SLOT_BUILDERS.items():
        slot = by_slot_name[slot_name]

        provider_id = slot.get("provider_id")
        if not provider_id:
            raise LLMConfigNotConfigured(
                f"User {user_id!r} slot {slot_name!r} has no provider_id."
            )

        row = await db.get_one("user_providers", {"provider_id": provider_id})
        if not row:
            raise LLMConfigNotConfigured(
                f"User {user_id!r} slot {slot_name!r} → provider {provider_id!r} "
                f"not found. The provider may have been deleted."
            )

        card = ProviderCard.from_row(row)

        if not _is_visible(card, user_id):
            raise LLMConfigNotConfigured(
                f"User {user_id!r} slot {slot_name!r} → provider {provider_id!r} "
                f"is not visible (owned by {card.owner_user_id!r})."
            )

        if not card.is_active:
            raise LLMConfigNotConfigured(
                f"User {user_id!r} slot {slot_name!r} → provider {provider_id!r} "
                f"is inactive."
            )

        # Self-heal: rewrites slot in-place if model ∉ card.models, with
        # 24h debounce + notification. card is unchanged.
        card, slot = await self_heal_if_broken(card, slot, db)

        # Phase 0 graceful fallback: if a row was added before backfill
        # ran, its driver_type is still null. Try to derive on the fly
        # so the resolve path still works during the migration window.
        driver_type = card.driver_type
        if not driver_type:
            from xyz_agent_context.agent_framework.provider_driver.derive import (
                derive_driver_type,
            )
            driver_type = derive_driver_type(
                card.source, card.auth_type, card.protocol
            )
            if driver_type:
                # Best-effort persist so we don't re-derive every call.
                try:
                    await db.update(
                        "user_providers",
                        {"provider_id": card.provider_id},
                        {"driver_type": driver_type},
                    )
                except Exception as e:  # noqa: BLE001
                    logger.warning(
                        f"[resolver] Failed to persist on-the-fly driver_type "
                        f"for provider {card.provider_id!r}: {e}"
                    )

        if not driver_type:
            raise LLMConfigNotConfigured(
                f"User {user_id!r} slot {slot_name!r} → provider {provider_id!r}: "
                f"cannot determine driver_type from (source={card.source!r}, "
                f"auth_type={card.auth_type!r}, protocol={card.protocol!r})."
            )

        driver_cls = get_driver_class(driver_type)
        if driver_cls is None:
            raise LLMConfigNotConfigured(
                f"User {user_id!r} slot {slot_name!r} → unknown driver_type "
                f"{driver_type!r}. (Are you running cloud-only driver in local mode?)"
            )

        driver = driver_cls(card)
        if slot_name == "agent" and _is_codex_framework(_agent_framework_from_slot(slot)):
            thinking, reasoning_effort = _slot_reasoning_params(slot)
            try:
                cfgs["codex"] = _codex_config_from_card(
                    card, slot["model"],
                    thinking=thinking, reasoning_effort=reasoning_effort,
                )
                cfgs[slot_name] = ClaudeConfig()
            except NotImplementedError as e:
                raise LLMConfigNotConfigured(
                    f"User {user_id!r} slot {slot_name!r}: driver "
                    f"{driver_type!r} cannot satisfy this slot ({e})."
                ) from e
            continue

        # helper_llm dispatches on the provider's protocol: an anthropic
        # provider routes to the Messages-API helper (single-Claude-key
        # path); openai keeps the existing Chat-Completions helper.
        if slot_name == "helper_llm" and (card.protocol or "").lower() == "anthropic":
            try:
                cfgs["helper_anthropic"] = driver.build_anthropic_helper_config(
                    slot["model"]
                )
            except NotImplementedError as e:
                raise LLMConfigNotConfigured(
                    f"User {user_id!r} slot {slot_name!r}: driver "
                    f"{driver_type!r} cannot satisfy this slot ({e})."
                ) from e
            continue

        builder = getattr(driver, builder_method)
        try:
            cfgs[slot_name] = builder(slot["model"])
        except NotImplementedError as e:
            raise LLMConfigNotConfigured(
                f"User {user_id!r} slot {slot_name!r}: driver "
                f"{driver_type!r} cannot satisfy this slot ({e})."
            ) from e

        # Thread the agent slot's framework-neutral reasoning params into
        # the built ClaudeConfig. Drivers don't take params (they only
        # know the provider card); the slot-level knobs are patched in
        # here — previously only the legacy fallback path honored them.
        if slot_name == "agent":
            thinking, reasoning_effort = _slot_reasoning_params(slot)
            if thinking or reasoning_effort:
                cfgs[slot_name] = dataclasses.replace(
                    cfgs[slot_name],  # type: ignore[arg-type]
                    thinking=thinking,
                    reasoning_effort=reasoning_effort,
                )

    return RuntimeLLMConfigs(
        claude=cfgs["agent"],          # type: ignore[arg-type]
        # When the helper runs on anthropic, ``openai`` stays an empty
        # default and is unused — get_helper_sdk dispatches off
        # ``anthropic_helper`` being set.
        openai=cfgs.get("helper_llm") or OpenAIConfig(),  # type: ignore[arg-type]
        codex=cfgs.get("codex", CodexConfig()),  # type: ignore[arg-type]
        anthropic_helper=cfgs.get("helper_anthropic"),  # type: ignore[arg-type]
    )


__all__ = ["resolve_user_llm_configs", "resolve_user_runtime_llm_configs"]
