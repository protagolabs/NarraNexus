"""
@file_name: test_system_provider_service.py
@author: Bin Liang
@date: 2026-04-16
@description: SystemProviderService env loading + is_enabled gating tests.

Verifies the service activates only when BOTH (a) the backend is in cloud
mode AND (b) the LiteLLM gateway coordinates + slot models are present.
Gateway mode (2026-07): the free tier no longer reads a raw master key;
enablement keys on SYSTEM_DEFAULT_LLM_GATEWAY_URL + _GATEWAY_ADMIN_KEY, the
agent slot carries an EMPTY api_key placeholder (per-run minted), and the
helper slot uses the backend-resident gateway key.
"""
import pytest

from xyz_agent_context.agent_framework.providers.system_service import (
    SystemProviderService,
)


@pytest.fixture(autouse=True)
def _reset_singleton():
    SystemProviderService._instance = None
    yield
    SystemProviderService._instance = None


# Full, valid gateway-mode env (minus DATABASE_URL, added by _set_cloud_env).
_FULL_ENV = {
    "SYSTEM_DEFAULT_LLM_ENABLED": "true",
    "SYSTEM_DEFAULT_LLM_SOURCE": "netmind",
    "SYSTEM_DEFAULT_LLM_GATEWAY_URL": "http://litellm:4000",
    "SYSTEM_DEFAULT_LLM_GATEWAY_ADMIN_KEY": "sk-master-admin",
    "SYSTEM_DEFAULT_LLM_GATEWAY_BACKEND_KEY": "sk-backend-helper",
    "SYSTEM_DEFAULT_LLM_ANTHROPIC_BASE_URL": "http://litellm:4000",
    "SYSTEM_DEFAULT_LLM_OPENAI_BASE_URL": "http://litellm:4000/v1",
    "SYSTEM_DEFAULT_LLM_AGENT_MODEL": "claude-sonnet-4-5",
    "SYSTEM_DEFAULT_LLM_HELPER_MODEL": "gpt-4o-mini",
}


def _set_cloud_env(monkeypatch, **kv):
    monkeypatch.setenv("DATABASE_URL", "mysql://u:p@h:3306/d")
    for k, v in kv.items():
        if v is None:
            monkeypatch.delenv(k, raising=False)
        else:
            monkeypatch.setenv(k, v)


def _set_full(monkeypatch, **overrides):
    """Cloud + full gateway env, with per-test overrides (None => unset)."""
    env = {**_FULL_ENV, **overrides}
    _set_cloud_env(monkeypatch, **env)


def test_disabled_when_enabled_flag_unset(monkeypatch):
    _set_full(monkeypatch, SYSTEM_DEFAULT_LLM_ENABLED=None)
    assert SystemProviderService.instance().is_enabled() is False


def test_disabled_in_local_mode_even_with_full_env(monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("DB_HOST", raising=False)
    for k, v in _FULL_ENV.items():
        monkeypatch.setenv(k, v)
    assert SystemProviderService.instance().is_enabled() is False


def test_disabled_when_gateway_url_missing(monkeypatch):
    _set_full(monkeypatch, SYSTEM_DEFAULT_LLM_GATEWAY_URL=None)
    assert SystemProviderService.instance().is_enabled() is False


def test_disabled_when_gateway_admin_key_missing(monkeypatch):
    _set_full(monkeypatch, SYSTEM_DEFAULT_LLM_GATEWAY_ADMIN_KEY=None)
    assert SystemProviderService.instance().is_enabled() is False


def test_disabled_when_slot_model_missing(monkeypatch):
    _set_full(monkeypatch, SYSTEM_DEFAULT_LLM_HELPER_MODEL=None)
    assert SystemProviderService.instance().is_enabled() is False


def test_disabled_when_source_invalid(monkeypatch):
    _set_full(monkeypatch, SYSTEM_DEFAULT_LLM_SOURCE="not-a-real-source")
    assert SystemProviderService.instance().is_enabled() is False


def test_enabled_and_config_constructed_when_all_env_set(monkeypatch):
    _set_full(monkeypatch)
    svc = SystemProviderService.instance()
    assert svc.is_enabled() is True
    cfg = svc.get_config()
    assert set(cfg.slots.keys()) == {"agent", "helper_llm"}
    assert cfg.slots["agent"].model == "claude-sonnet-4-5"
    assert cfg.slots["helper_llm"].model == "gpt-4o-mini"

    agent_prov = cfg.providers[cfg.slots["agent"].provider_id]
    helper_prov = cfg.providers[cfg.slots["helper_llm"].provider_id]
    # Agent slot: NEVER a durable key here — per-run session key injected at spawn.
    assert agent_prov.api_key == ""
    # Helper slot: backend-resident gateway key (NOT the master key).
    assert helper_prov.api_key == "sk-backend-helper"
    # Both point at the gateway, not the upstream provider.
    assert agent_prov.base_url == "http://litellm:4000"
    assert helper_prov.base_url == "http://litellm:4000/v1"


def test_helper_key_optional_agent_slot_still_enabled(monkeypatch):
    # Absent backend helper key must not disable the free tier; agent slot works.
    _set_full(monkeypatch, SYSTEM_DEFAULT_LLM_GATEWAY_BACKEND_KEY=None)
    svc = SystemProviderService.instance()
    assert svc.is_enabled() is True
    cfg = svc.get_config()
    assert cfg.providers[cfg.slots["helper_llm"].provider_id].api_key == ""


def test_get_config_raises_when_disabled(monkeypatch):
    _set_full(monkeypatch, SYSTEM_DEFAULT_LLM_ENABLED=None)
    svc = SystemProviderService.instance()
    with pytest.raises(RuntimeError):
        svc.get_config()


def test_get_initial_quota_reads_env(monkeypatch):
    _set_cloud_env(
        monkeypatch,
        SYSTEM_DEFAULT_QUOTA_INPUT_TOKENS="500000",
        SYSTEM_DEFAULT_QUOTA_OUTPUT_TOKENS="100000",
    )
    assert SystemProviderService.instance().get_initial_quota() == (500_000, 100_000)


def test_get_initial_quota_defaults_to_zero_when_unset(monkeypatch):
    _set_cloud_env(
        monkeypatch,
        SYSTEM_DEFAULT_QUOTA_INPUT_TOKENS=None,
        SYSTEM_DEFAULT_QUOTA_OUTPUT_TOKENS=None,
    )
    assert SystemProviderService.instance().get_initial_quota() == (0, 0)
