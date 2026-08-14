"""
@file_name: test_platform_origin_binding.py
@date: 2026-08-13
@description: Platform-origin binding — the app half. The broker identity token
must be emitted as X-NarraNexus-Identity-Token ONLY to our own gateway, on both
outbound legs (claude_code CLI env + nexus_power litellm extra_headers), and must
ride provider_configs across the executor wire.
"""
from __future__ import annotations

from xyz_agent_context.agent_framework.api_config import (
    ClaudeConfig,
    CodexConfig,
    OpenAIConfig,
    bind_platform_identity,
    set_user_config,
    snapshot_user_config,
)
from xyz_agent_context.agent_framework.adapters.nexus.nexus_agent import (
    NexusAgent,
    claude_config,
)
from xyz_agent_context.agent_runtime.executor_protocol import (
    apply_provider_configs,
    serialize_provider_configs,
)

_GW = "http://litellm:4000"          # our own free-tier gateway
_BYOK = "https://api.anthropic.com"  # a third party
_HEADER = "X-NarraNexus-Identity-Token"


# ---- claude_code CLI leg (to_cli_env) -------------------------------------

def test_cli_env_emits_identity_header_to_own_gateway():
    env = ClaudeConfig(api_key="k", base_url=_GW, identity_token="tok-1").to_cli_env()
    assert env["ANTHROPIC_CUSTOM_HEADERS"] == f"{_HEADER}: tok-1"


def test_cli_env_omits_identity_header_off_platform():
    env = ClaudeConfig(api_key="k", base_url=_BYOK, identity_token="tok-1").to_cli_env()
    assert "ANTHROPIC_CUSTOM_HEADERS" not in env


def test_cli_env_omits_identity_header_without_token():
    env = ClaudeConfig(api_key="k", base_url=_GW).to_cli_env()
    assert "ANTHROPIC_CUSTOM_HEADERS" not in env


# ---- bind + executor-wire round trip --------------------------------------

def _fresh_configs(base_url):
    set_user_config(
        claude=ClaudeConfig(api_key="k", base_url=base_url, model="m"),
        openai=OpenAIConfig(api_key="k", base_url=base_url),
        codex=CodexConfig(api_key="k", base_url=base_url, model="m"),
    )


def test_bind_stamps_token_onto_all_configs_and_survives_the_wire():
    _fresh_configs(_GW)
    bind_platform_identity("tok-2")
    serialized = serialize_provider_configs()
    assert serialized["claude"]["identity_token"] == "tok-2"
    assert serialized["codex"]["identity_token"] == "tok-2"
    assert serialized["openai"]["identity_token"] == "tok-2"
    # re-apply on the "executor" side → token preserved on the ContextVar configs
    apply_provider_configs(serialized)
    snap = snapshot_user_config()
    assert snap["claude"].identity_token == "tok-2"


def test_bind_noop_without_token():
    _fresh_configs(_GW)
    bind_platform_identity("")
    assert serialize_provider_configs()["claude"]["identity_token"] == ""


def test_apply_tolerates_unknown_wire_fields():
    # Rolling-deploy skew: a NEW orchestrator serializes a field an OLD executor
    # dataclass lacks. apply must drop it, not crash the turn.
    payload = {
        "claude": {"api_key": "k", "base_url": _GW, "future_field_x": "boom"},
        "openai": None,
        "codex": None,
        "anthropic_helper": None,
        "cli_helper": None,
    }
    apply_provider_configs(payload)  # must not raise
    assert snapshot_user_config()["claude"].api_key == "k"


# ---- nexus_power leg (_build_request_payload) -----------------------------

def _nexus_slot(monkeypatch, *, base_url, token):
    monkeypatch.setattr(claude_config, "model", "deepseek-v4-flash")
    monkeypatch.setattr(claude_config, "api_key", "k")
    monkeypatch.setattr(claude_config, "base_url", base_url)
    monkeypatch.setattr(claude_config, "auth_type", "api_key")
    monkeypatch.setattr(claude_config, "thinking", "")
    monkeypatch.setattr(claude_config, "identity_token", token)


def _payload():
    return NexusAgent(working_path="/tmp")._build_request_payload(
        messages=[{"role": "user", "content": "hi"}],
        mcp_servers={},
        extra_env=None,
        kwargs={"agent_id": "a1"},
    )


def test_nexus_emits_identity_header_to_own_gateway(monkeypatch):
    _nexus_slot(monkeypatch, base_url=_GW, token="tok-3")
    headers = _payload()["options"]["llm_extra"]["extra_headers"]
    assert headers[_HEADER] == "tok-3"


def test_nexus_omits_identity_header_off_platform(monkeypatch):
    _nexus_slot(monkeypatch, base_url=_BYOK, token="tok-3")
    extra_headers = _payload()["options"]["llm_extra"].get("extra_headers", {})
    assert _HEADER not in extra_headers
