"""
@file_name: user_provider_service.py
@author: NexusAgent
@date: 2026-04-08
@description: Per-user LLM provider configuration service

Manages provider and slot configurations per user in the database.
Replaces the global llm_config.json for multi-tenant cloud deployments.

In local mode (SQLite), falls back to llm_config.json for backward compatibility.
In cloud mode (MySQL), all provider configs are stored in user_providers and user_slots tables.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from uuid import uuid4

from loguru import logger

from xyz_agent_context.schema.provider_schema import (
    LLMConfig,
    ProviderConfig,
    SlotConfig,
    SlotName,
    get_slot_required_protocols,
)


def _is_cloud_mode() -> bool:
    """Deprecated local name; routes to the single source of truth in
    ``utils.deployment_mode``. Kept as a thin adapter so any external
    callers that import this symbol keep working."""
    from xyz_agent_context.utils.deployment_mode import is_cloud_mode
    return is_cloud_mode()


# Canonical curated model list for the codex_cli agent framework,
# regardless of which OpenAI-protocol provider supplies the credential.
# Codex CLI's interactive picker decides which models its subprocess
# accepts — same set for ChatGPT-account OAuth AND paid-API-key tier
# (verified 2026-06-02). Custom OpenAI providers used with
# ``agent_framework=codex_cli`` must be constrained to this list too,
# otherwise the frontend dropdown offers e.g. ``o4-mini`` which the
# codex CLI subprocess rejects.
#
# Two consumers:
#   1. ``UserProviderService.get_user_config`` overrides the stored
#      ``models`` column on a ``codex_oauth`` row at read time, so
#      updating the constant propagates without DB migration.
#   2. Frontend slot dropdown filters by this list when the agent
#      slot's ``agent_framework == "codex_cli"``, irrespective of
#      provider source (codex_oauth, custom openai, etc.).
#
# Verified 2026-06-02 by running interactive ``codex`` and reading
# "Select Model and Effort" menu.
CODEX_CURATED_MODELS = ["gpt-5.5", "gpt-5.4", "gpt-5.4-mini"]


def _generate_provider_id() -> str:
    return f"prov_{uuid4().hex[:8]}"


def _generate_group_id() -> str:
    return f"grp_{uuid4().hex[:8]}"


class UserProviderService:
    """
    Per-user provider management via database.

    Methods mirror provider_registry's API but are scoped to a user_id.
    """

    def __init__(self, db_client):
        self.db = db_client

    # =========================================================================
    # Read
    # =========================================================================

    async def get_user_config(self, user_id: str) -> LLMConfig:
        """Load a user's provider config from DB, returning an LLMConfig object."""
        # Get providers
        rows = await self.db.get("user_providers", filters={"user_id": user_id})
        providers = {}
        for row in rows:
            # supports_anthropic_server_tools is a newer column. Old rows
            # pre-dating the migration won't have it; default False so we
            # err on the side of disabling WebSearch rather than hanging it.
            _server_tools = row.get("supports_anthropic_server_tools", 0)
            # ``codex_oauth`` provider's model list is NOT user-customizable —
            # codex CLI's interactive picker is the source of truth. Always
            # override the stored ``models`` column with the current
            # ``CODEX_CURATED_MODELS`` constant so a code-side update
            # propagates to existing rows on the next Settings reload, no
            # DB migration needed.
            if row.get("source") == "codex_oauth":
                stored_models = list(CODEX_CURATED_MODELS)
            elif row.get("models"):
                stored_models = json.loads(row["models"])
            else:
                stored_models = []
            prov = ProviderConfig(
                provider_id=row["provider_id"],
                name=row["name"],
                source=row["source"],
                protocol=row["protocol"],
                auth_type=row.get("auth_type", "api_key"),
                api_key=row.get("api_key", ""),
                base_url=row.get("base_url", ""),
                models=stored_models,
                linked_group=row.get("linked_group", ""),
                is_active=bool(row.get("is_active", 1)),
                supports_anthropic_server_tools=bool(_server_tools),
            )
            providers[prov.provider_id] = prov

        # Get slots
        slot_rows = await self.db.get("user_slots", filters={"user_id": user_id})
        slots = {}
        for row in slot_rows:
            slots[row["slot_name"]] = SlotConfig(
                provider_id=row["provider_id"],
                model=row["model"],
            )

        return LLMConfig(providers=providers, slots=slots)

    # =========================================================================
    # Add Provider
    # =========================================================================

    async def add_provider(
        self,
        user_id: str,
        card_type: str,
        name: str = "",
        api_key: str = "",
        base_url: str = "",
        auth_type: str = "api_key",
        models: Optional[List[str]] = None,
    ) -> tuple[LLMConfig, list[str]]:
        """Add a provider for a user. Returns (updated_config, new_provider_ids)."""

        new_ids: list[str] = []
        now = datetime.now(timezone.utc).isoformat()

        if card_type in ("netmind", "yunwu", "openrouter"):
            # Check uniqueness
            existing = await self.db.get("user_providers", filters={"user_id": user_id, "source": card_type})
            if existing:
                raise ValueError(f"A {card_type} provider already exists for this user")

            group_id = _generate_group_id()
            configs = _build_dual_providers(card_type, api_key, group_id, models)
            for cfg in configs:
                await self._insert_provider(user_id, cfg, now)
                new_ids.append(cfg["provider_id"])

        elif card_type == "claude_oauth":
            existing = await self.db.get("user_providers", filters={"user_id": user_id, "source": "claude_oauth"})
            if existing:
                raise ValueError("Claude OAuth provider already exists for this user")

            pid = _generate_provider_id()
            await self._insert_provider(user_id, {
                "provider_id": pid,
                "name": "Claude Code (OAuth)",
                "source": "claude_oauth",
                "protocol": "anthropic",
                "auth_type": "oauth",
                "api_key": "",
                "base_url": "",
                "models": json.dumps(["claude-opus-4-7", "claude-sonnet-4-6"]),
                # OAuth funnels through official Anthropic → server tools OK.
                "supports_anthropic_server_tools": True,
            }, now)
            new_ids.append(pid)

        elif card_type == "codex_oauth":
            from xyz_agent_context.agent_framework.provider_driver.derive import (
                CODEX_CLI_CREDENTIALS_REF,
            )

            # Mirror of claude_oauth: a single row representing the
            # host's ``codex login`` credential. The CodexSDK reads
            # the token directly from ~/.codex/auth.json via its
            # own OAuth fallback; this row exists primarily so
            # CodexOAuthDriver.probe() has a card to probe and the
            # Settings page can surface ✓ / ✗ status.
            #
            # protocol="openai" because Codex's underlying API surface
            # is OpenAI-compatible (Responses API). This makes the row
            # technically eligible for the helper_llm slot too — but
            # OAuth credentials can't actually serve chat-completions
            # calls, so users shouldn't pin it there. We don't gate
            # it at insert time; the resolver / driver layers reject
            # non-agent-slot uses with NotImplementedError.
            existing = await self.db.get(
                "user_providers", filters={"user_id": user_id, "source": "codex_oauth"}
            )
            if existing:
                raise ValueError("Codex OAuth provider already exists for this user")

            pid = _generate_provider_id()
            await self._insert_provider(user_id, {
                "provider_id": pid,
                "name": "Codex CLI (OAuth)",
                "source": "codex_oauth",
                "protocol": "openai",
                "auth_type": "oauth",
                "api_key": "",
                "base_url": "",
                # Seed the column for completeness, but the read path
                # in ``get_user_config`` overrides this with the
                # current ``CODEX_CURATED_MODELS`` constant —
                # so the column value is effectively cached, not
                # canonical. Updating the constant updates the
                # dropdown for every existing user on next reload.
                "models": json.dumps(list(CODEX_CURATED_MODELS)),
                # Codex is OpenAI's product — Anthropic server tools
                # (WebSearch etc.) are not applicable.
                "supports_anthropic_server_tools": False,
                "driver_type": "codex_oauth",
                "billing_policy": "external_oauth",
                "auth_ref": CODEX_CLI_CREDENTIALS_REF,
            }, now)
            new_ids.append(pid)

        elif card_type in ("anthropic", "openai"):
            pid = _generate_provider_id()
            display_name = name or f"Custom {card_type.title()}"
            if not models:
                from xyz_agent_context.agent_framework.model_catalog import get_default_models
                models = get_default_models("user", card_type)
            else:
                models = list(models)
            # Always surface the official embedding models for an OpenAI-protocol
            # provider (api.openai.com OR an OpenAI-compatible forward), so the
            # embedding slot has candidates even when the user listed only chat
            # models. Vendor presets (NetMind etc.) carry their own embeddings
            # via _build_dual_providers and never reach this branch.
            if card_type == "openai":
                from xyz_agent_context.agent_framework.model_catalog import (
                    get_default_embedding_models,
                )
                for _em in get_default_embedding_models("openai"):
                    if _em not in models:
                        models.append(_em)
            # Auto-detect: only the official api.anthropic.com host serves
            # the server-side tool suite (WebSearch etc.). User can flip
            # this later via the edit-provider flow if they front official
            # with a transparent proxy.
            server_tools = (
                card_type == "anthropic"
                and "api.anthropic.com" in (base_url or "").lower()
            )
            await self._insert_provider(user_id, {
                "provider_id": pid,
                "name": display_name,
                "source": "user",
                "protocol": card_type,
                "auth_type": auth_type,
                "api_key": api_key,
                "base_url": base_url,
                "models": json.dumps(models or []),
                "supports_anthropic_server_tools": server_tools,
            }, now)
            new_ids.append(pid)
        else:
            raise ValueError(f"Unknown card_type: {card_type}")

        config = await self.get_user_config(user_id)
        return config, new_ids

    async def _insert_provider(self, user_id: str, data: dict, now: str):
        row = {
            "user_id": user_id,
            "provider_id": data["provider_id"],
            "name": data["name"],
            "source": data["source"],
            "protocol": data["protocol"],
            "auth_type": data.get("auth_type", "api_key"),
            "api_key": data.get("api_key", ""),
            "base_url": data.get("base_url", ""),
            "models": data.get("models", "[]"),
            "linked_group": data.get("linked_group", ""),
            "is_active": 1,
            "supports_anthropic_server_tools": 1 if data.get("supports_anthropic_server_tools") else 0,
            "created_at": now,
            "updated_at": now,
        }
        for optional_key in ("driver_type", "owner_user_id", "billing_policy", "auth_ref"):
            if optional_key in data:
                row[optional_key] = data[optional_key]
        await self.db.insert("user_providers", row)

    # =========================================================================
    # Remove Provider
    # =========================================================================

    async def remove_provider(self, user_id: str, provider_id: str) -> LLMConfig:
        """Remove a provider (and its linked group). Clears affected slots."""
        row = await self.db.get_one("user_providers", {"user_id": user_id, "provider_id": provider_id})
        if not row:
            raise ValueError(f"Provider {provider_id} not found")

        # If linked group, delete all in group
        linked_group = row.get("linked_group", "")
        if linked_group:
            group_rows = await self.db.get("user_providers", {"user_id": user_id, "linked_group": linked_group})
            for r in group_rows:
                await self.db.delete("user_providers", {"user_id": user_id, "provider_id": r["provider_id"]})
                # Clear any slots using this provider
                await self.db.delete("user_slots", {"user_id": user_id, "provider_id": r["provider_id"]})
        else:
            await self.db.delete("user_providers", {"user_id": user_id, "provider_id": provider_id})
            await self.db.delete("user_slots", {"user_id": user_id, "provider_id": provider_id})

        return await self.get_user_config(user_id)

    # =========================================================================
    # Slots
    # =========================================================================

    async def set_slot(self, user_id: str, slot_name: str, provider_id: str, model: str) -> LLMConfig:
        """Assign a provider + model to a slot for a user."""
        # Validate slot name
        if slot_name not in [s.value for s in SlotName]:
            raise ValueError(f"Invalid slot: {slot_name}")

        # Validate provider exists for this user
        prov = await self.db.get_one("user_providers", {"user_id": user_id, "provider_id": provider_id})
        if not prov:
            raise ValueError(f"Provider {provider_id} not found for user {user_id}")

        # Validate protocol. The agent slot is framework-dependent:
        # claude_code requires Anthropic protocol, codex_cli requires
        # OpenAI protocol. Other slots keep their static requirements.
        agent_framework = None
        if slot_name == SlotName.AGENT.value:
            existing_slot = await self.db.get_one(
                "user_slots", {"user_id": user_id, "slot_name": slot_name}
            )
            agent_framework = (existing_slot or {}).get("agent_framework") or "claude_code"
        required = get_slot_required_protocols(
            slot_name,
            agent_framework=agent_framework,
        )
        if required and prov["protocol"] not in [p.value for p in required]:
            raise ValueError(f"Slot '{slot_name}' requires protocol {[p.value for p in required]}, got '{prov['protocol']}'")

        # Upsert slot
        existing = await self.db.get_one("user_slots", {"user_id": user_id, "slot_name": slot_name})
        now = datetime.now(timezone.utc).isoformat()
        if existing:
            await self.db.update("user_slots",
                {"user_id": user_id, "slot_name": slot_name},
                {"provider_id": provider_id, "model": model, "updated_at": now}
            )
        else:
            await self.db.insert("user_slots", {
                "user_id": user_id,
                "slot_name": slot_name,
                "provider_id": provider_id,
                "model": model,
                "updated_at": now,
            })

        return await self.get_user_config(user_id)

    # ---- agent_framework: per-user coding-agent SDK choice ---------
    # The ``user_slots[slot_name='agent'].agent_framework`` column is
    # read by step_3_agent_loop._resolve_agent_framework_sdk to pick
    # ClaudeAgentSDK vs CodexSDK. Reading defaults to "claude_code"
    # for any null/missing row so existing users are untouched.

    _SUPPORTED_AGENT_FRAMEWORKS: tuple[str, ...] = ("claude_code", "codex_cli")

    async def get_user_agent_framework(self, user_id: str) -> str:
        """Return the user's chosen coding-agent framework.

        Returns ``"claude_code"`` when:
          - The user has no agent slot row yet (new user)
          - The column is null (rows from before the column was added)
        Anything other than the supported set still returns the raw
        value; the caller (step_3 resolver) is responsible for
        falling back to claude_code on unknown values.
        """
        row = await self.db.get_one(
            "user_slots", {"user_id": user_id, "slot_name": "agent"}
        )
        if not row:
            return "claude_code"
        return (row.get("agent_framework") or "claude_code")

    async def set_user_agent_framework(self, user_id: str, framework: str) -> None:
        """Persist the user's coding-agent framework choice.

        Upserts ``user_slots[user_id, slot_name='agent'].agent_framework``.
        If the user has no agent slot row yet (provider_id/model not
        set), a stub row is inserted with empty provider_id+model so
        the framework choice is preserved until they wire the slot.
        provider_resolver still rejects the call at agent_loop time
        when provider_id is empty — same as today.

        Raises ``ValueError`` for unknown framework values.
        """
        if framework not in self._SUPPORTED_AGENT_FRAMEWORKS:
            raise ValueError(
                f"Unknown agent_framework {framework!r}. "
                f"Supported: {self._SUPPORTED_AGENT_FRAMEWORKS}"
            )

        existing = await self.db.get_one(
            "user_slots", {"user_id": user_id, "slot_name": "agent"}
        )
        now = datetime.now(timezone.utc).isoformat()
        if existing:
            await self.db.update(
                "user_slots",
                {"user_id": user_id, "slot_name": "agent"},
                {"agent_framework": framework, "updated_at": now},
            )
        else:
            # Stub row: framework choice survives even if the agent
            # slot's provider/model is not yet wired. provider_resolver
            # will reject usage with empty provider_id at agent_loop
            # time, which is correct UX (forces the user to finish
            # slot setup).
            await self.db.insert(
                "user_slots",
                {
                    "user_id": user_id,
                    "slot_name": "agent",
                    "provider_id": "",
                    "model": "",
                    "agent_framework": framework,
                    "updated_at": now,
                },
            )

    async def validate_slots(self, user_id: str) -> list[str]:
        """Validate all slots are configured."""
        config = await self.get_user_config(user_id)
        errors = []
        for slot in SlotName:
            if slot.value not in config.slots:
                errors.append(f"Slot '{slot.value}' not configured")
        return errors

    # =========================================================================
    # Update Models
    # =========================================================================

    async def update_models(self, user_id: str, provider_id: str, models: list[str]) -> LLMConfig:
        """Update available models for a provider."""
        now = datetime.now(timezone.utc).isoformat()
        affected = await self.db.update("user_providers",
            {"user_id": user_id, "provider_id": provider_id},
            {"models": json.dumps(models), "updated_at": now}
        )
        if affected == 0:
            raise ValueError(f"Provider {provider_id} not found")
        return await self.get_user_config(user_id)

    # =========================================================================
    # Test
    # =========================================================================

    async def test_provider(self, user_id: str, provider_id: str) -> tuple[bool, str]:
        """Test connectivity to a provider."""
        row = await self.db.get_one("user_providers", {"user_id": user_id, "provider_id": provider_id})
        if not row:
            return False, "Provider not found"

        if row.get("auth_type") == "oauth":
            return True, "OAuth provider (managed by Claude Code CLI)"

        from xyz_agent_context.agent_framework.provider_registry import provider_registry
        prov = ProviderConfig(
            provider_id=row["provider_id"],
            name=row["name"],
            source=row["source"],
            protocol=row["protocol"],
            auth_type=row.get("auth_type", "api_key"),
            api_key=row.get("api_key", ""),
            base_url=row.get("base_url", ""),
            models=json.loads(row["models"]) if row.get("models") else [],
        )
        return await provider_registry.test_provider(prov)


# =============================================================================
# Dual-protocol provider builder (NetMind, Yunwu, OpenRouter)
# =============================================================================

_DUAL_PROVIDER_CONFIGS = {
    "netmind": {
        "anthropic": {"name": "NetMind (Anthropic)", "base_url": "https://api.netmind.ai/inference-api/anthropic", "auth_type": "bearer_token"},
        "openai": {"name": "NetMind (OpenAI)", "base_url": "https://api.netmind.ai/inference-api/openai/v1", "auth_type": "api_key"},
    },
    "yunwu": {
        "anthropic": {"name": "Yunwu (Anthropic)", "base_url": "https://api.yunwuai.cloud/v1/messages", "auth_type": "api_key"},
        "openai": {"name": "Yunwu (OpenAI)", "base_url": "https://api.yunwuai.cloud/v1", "auth_type": "api_key"},
    },
    "openrouter": {
        "anthropic": {"name": "OpenRouter (Anthropic)", "base_url": "https://openrouter.ai/api/v1/messages", "auth_type": "api_key"},
        "openai": {"name": "OpenRouter (OpenAI)", "base_url": "https://openrouter.ai/api/v1", "auth_type": "api_key"},
    },
}


def _build_dual_providers(card_type: str, api_key: str, group_id: str, models: Optional[list] = None) -> list[dict]:
    from xyz_agent_context.agent_framework.model_catalog import get_default_models
    cfg = _DUAL_PROVIDER_CONFIGS[card_type]
    result = []
    for protocol, info in cfg.items():
        proto_models = models or get_default_models(card_type, protocol)
        result.append({
            "provider_id": _generate_provider_id(),
            "name": info["name"],
            "source": card_type,
            "protocol": protocol,
            "auth_type": info["auth_type"],
            "api_key": api_key,
            "base_url": info["base_url"],
            "models": json.dumps(proto_models),
            "linked_group": group_id,
        })
    return result
