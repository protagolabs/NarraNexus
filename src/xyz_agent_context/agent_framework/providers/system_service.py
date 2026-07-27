"""
@file_name: system_service.py
@author: Bin Liang
@date: 2026-04-16
@description: Load the system-default LLMConfig from environment variables.

Activates ONLY in cloud mode AND when all required env vars are present.
In local mode or when disabled, is_enabled() returns False and every
caller should short-circuit — this preserves the local `bash run.sh`
experience unchanged.

Gateway mode (2026-07): the free tier no longer holds the Power master key
in this process. The config it exposes points both protocol slots at the
LiteLLM gateway; the agent slot carries an empty api_key placeholder (the
real per-run session key is minted on the BACKEND by
gateway_key_service.open_backend_session — called from step_3_agent_loop, NOT
in the executor — and injected into the ClaudeConfig ContextVar so it rides
provider_configs to the executor), and the helper slot uses a backend-resident
gateway key (not the master). The master key lives only inside the gateway
container.
"""
from __future__ import annotations

import os
from typing import Optional, Tuple

from xyz_agent_context.schema.provider_schema import (
    AuthType,
    LLMConfig,
    ProviderConfig,
    ProviderProtocol,
    ProviderSource,
    SlotConfig,
)


_SYSTEM_ANTHROPIC_PROVIDER_ID = "system_default_anthropic"
_SYSTEM_OPENAI_PROVIDER_ID = "system_default_openai"


def _is_cloud_mode() -> bool:
    """Thin wrapper preserved for file-local readability; routes to the
    single source of truth in ``utils.deployment_mode``. Honours the
    same explicit ``NARRANEXUS_DEPLOYMENT_MODE`` env var as the rest of
    the codebase.

    Also keeps a DB_HOST fallback for existing cloud deployments that
    set DB_HOST but haven't set NARRANEXUS_DEPLOYMENT_MODE or
    DATABASE_URL — the canonical helper covers DATABASE_URL; we add
    DB_HOST on top to avoid regressing on those deployments.
    """
    from xyz_agent_context.utils.deployment_mode import is_cloud_mode
    if is_cloud_mode():
        return True
    return bool(os.environ.get("DB_HOST", ""))


class SystemProviderService:
    """Module-level singleton. Env is read once at first `instance()` call."""

    _instance: Optional["SystemProviderService"] = None

    def __init__(self, enabled: bool, config: Optional[LLMConfig]):
        self._enabled = enabled
        self._config = config

    @classmethod
    def instance(cls) -> "SystemProviderService":
        if cls._instance is None:
            cls._instance = cls._load_from_env()
        return cls._instance

    @classmethod
    def _load_from_env(cls) -> "SystemProviderService":
        if not _is_cloud_mode():
            return cls(enabled=False, config=None)
        if os.environ.get("SYSTEM_DEFAULT_LLM_ENABLED", "").lower() != "true":
            return cls(enabled=False, config=None)

        # Gateway mode — the ONLY supported free-tier mode. The Power master key
        # lives exclusively inside the LiteLLM gateway container; this process
        # never holds it. We require the gateway coordinates instead of a raw
        # key. Per-run session keys are minted on the BACKEND (step_3 via
        # gateway_key_service.open_backend_session), NOT in the executor; the
        # helper slot uses a backend-resident gateway key (not the master). See
        # module docstring + gateway_key_service.py.
        gateway_url = os.environ.get("SYSTEM_DEFAULT_LLM_GATEWAY_URL", "").strip()
        gateway_admin = os.environ.get(
            "SYSTEM_DEFAULT_LLM_GATEWAY_ADMIN_KEY", ""
        ).strip()
        if not (gateway_url and gateway_admin):
            return cls(enabled=False, config=None)

        agent_model = os.environ.get("SYSTEM_DEFAULT_LLM_AGENT_MODEL", "").strip()
        helper_model = os.environ.get("SYSTEM_DEFAULT_LLM_HELPER_MODEL", "").strip()
        if not (agent_model and helper_model):
            return cls(enabled=False, config=None)

        source_str = os.environ.get("SYSTEM_DEFAULT_LLM_SOURCE", "netmind").strip()
        try:
            source = ProviderSource(source_str)
        except ValueError:
            return cls(enabled=False, config=None)

        # Both protocols target the gateway (which forwards upstream with the
        # master key). base_url may be given per-protocol (path differences) or
        # default to the gateway root.
        anthropic_base = (
            os.environ.get("SYSTEM_DEFAULT_LLM_ANTHROPIC_BASE_URL", "").strip()
            or gateway_url
        )
        openai_base = (
            os.environ.get("SYSTEM_DEFAULT_LLM_OPENAI_BASE_URL", "").strip()
            or gateway_url
        )
        # Backend-resident gateway key for server-side helper_llm calls. NOT the
        # master key — a gateway key with a bounded blast radius. Optional: absent
        # → helper slot has no key and degrades, but the security-critical agent
        # slot (per-run minted) is unaffected.
        backend_helper_key = os.environ.get(
            "SYSTEM_DEFAULT_LLM_GATEWAY_BACKEND_KEY", ""
        ).strip()

        anthropic_provider = ProviderConfig(
            provider_id=_SYSTEM_ANTHROPIC_PROVIDER_ID,
            name="System Default (Anthropic via gateway)",
            source=source,
            protocol=ProviderProtocol.ANTHROPIC,
            auth_type=AuthType.BEARER_TOKEN,
            # Empty placeholder: the real per-run session key is injected into the
            # subprocess env at agent_loop spawn — never a durable key here.
            api_key="",
            base_url=anthropic_base,
            models=[agent_model],
            linked_group="system_default",
            is_active=True,
            supports_anthropic_server_tools=False,
        )
        openai_provider = ProviderConfig(
            provider_id=_SYSTEM_OPENAI_PROVIDER_ID,
            name="System Default (OpenAI via gateway)",
            source=source,
            protocol=ProviderProtocol.OPENAI,
            auth_type=AuthType.API_KEY,
            api_key=backend_helper_key,
            base_url=openai_base,
            models=[helper_model],
            linked_group="system_default",
            is_active=True,
        )

        cfg = LLMConfig(
            providers={
                _SYSTEM_ANTHROPIC_PROVIDER_ID: anthropic_provider,
                _SYSTEM_OPENAI_PROVIDER_ID: openai_provider,
            },
            slots={
                "agent": SlotConfig(
                    provider_id=_SYSTEM_ANTHROPIC_PROVIDER_ID,
                    model=agent_model,
                ),
                "helper_llm": SlotConfig(
                    provider_id=_SYSTEM_OPENAI_PROVIDER_ID,
                    model=helper_model,
                ),
            },
        )
        return cls(enabled=True, config=cfg)

    def is_enabled(self) -> bool:
        return self._enabled

    def get_config(self) -> LLMConfig:
        if not self._enabled or self._config is None:
            raise RuntimeError(
                "SystemProviderService is disabled; check is_enabled() first"
            )
        return self._config

    def get_initial_quota(self) -> Tuple[int, int]:
        """Read SYSTEM_DEFAULT_QUOTA_* from env. Safe to call even when disabled."""
        inp = int(os.environ.get("SYSTEM_DEFAULT_QUOTA_INPUT_TOKENS", "0"))
        out = int(os.environ.get("SYSTEM_DEFAULT_QUOTA_OUTPUT_TOKENS", "0"))
        return inp, out
