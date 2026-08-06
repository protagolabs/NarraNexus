"""
@file_name: test_helper_latency_swap.py
@author: Bin Liang
@date: 2026-08-06
@description: Latency-sensitive helper calls swap always-thinking slot
models for a fast non-thinking model on the same endpoint.

Background (measured 2026-08-06 against NetMind, via dev gateway):
DeepSeek-V4-Flash spends 1-5k reasoning tokens on a narrative-routing
arbitration (~15-30s wall) and NO request parameter disables it —
reasoning_effort / chat_template_kwargs / thinking are all ignored
upstream (and LiteLLM drop_params=true strips them anyway). The same
prompt on DeepSeek-V3.2 answers identically in ~2s with zero thinking.
So the only working lever is a model swap, scoped to endpoints where we
KNOW the fast model exists (NetMind + our LiteLLM gateway), and only
when the slot model is a known always-thinking family.
"""

import inspect

from xyz_agent_context.agent_framework.adapters import openai_agents as oa
from xyz_agent_context.agent_framework.adapters.openai_agents import (
    _latency_swap_model,
)


def _set(monkeypatch, base_url, slot_model):
    monkeypatch.setattr(oa.openai_config, "base_url", base_url)
    monkeypatch.setattr(oa.openai_config, "model", slot_model)


NETMIND = "https://api.netmind.ai/inference-api/openai/v1"
GATEWAY = "http://litellm:4000/v1"


def test_thinking_slot_on_netmind_swaps(monkeypatch):
    _set(monkeypatch, NETMIND, "deepseek-ai/DeepSeek-V4-Flash")
    assert _latency_swap_model("deepseek-ai/DeepSeek-V4-Flash") == "deepseek-ai/DeepSeek-V3.2"


def test_thinking_slot_on_gateway_swaps(monkeypatch):
    _set(monkeypatch, GATEWAY, "deepseek-ai/DeepSeek-V4-Pro")
    assert _latency_swap_model("deepseek-ai/DeepSeek-V4-Pro") == "deepseek-ai/DeepSeek-V3.2"


def test_non_thinking_slot_is_untouched(monkeypatch):
    _set(monkeypatch, NETMIND, "Qwen/Qwen3-Coder-480B-A35B-Instruct")
    assert (
        _latency_swap_model("Qwen/Qwen3-Coder-480B-A35B-Instruct")
        == "Qwen/Qwen3-Coder-480B-A35B-Instruct"
    )


def test_unknown_host_is_untouched(monkeypatch):
    # OpenRouter's deepseek ids live in a different namespace — a swap
    # there would 404. Hosts must be allowlisted.
    _set(monkeypatch, "https://openrouter.ai/api/v1", "deepseek-ai/DeepSeek-V4-Flash")
    assert (
        _latency_swap_model("deepseek-ai/DeepSeek-V4-Flash")
        == "deepseek-ai/DeepSeek-V4-Flash"
    )


def test_official_openai_is_untouched(monkeypatch):
    _set(monkeypatch, "https://api.openai.com/v1", "o3")
    assert _latency_swap_model("o3") == "o3"


def test_glm_and_minimax_families_swap(monkeypatch):
    _set(monkeypatch, NETMIND, "zai-org/GLM-5.2")
    assert _latency_swap_model("zai-org/GLM-5.2") == "deepseek-ai/DeepSeek-V3.2"
    assert _latency_swap_model("minimax/minimax-m3") == "deepseek-ai/DeepSeek-V3.2"


def test_env_empty_disables_swap(monkeypatch):
    _set(monkeypatch, NETMIND, "deepseek-ai/DeepSeek-V4-Flash")
    monkeypatch.setenv("HELPER_FAST_MODEL", "")
    assert (
        _latency_swap_model("deepseek-ai/DeepSeek-V4-Flash")
        == "deepseek-ai/DeepSeek-V4-Flash"
    )


def test_env_overrides_fast_model(monkeypatch):
    _set(monkeypatch, NETMIND, "deepseek-ai/DeepSeek-V4-Flash")
    monkeypatch.setenv("HELPER_FAST_MODEL", "deepseek-ai/DeepSeek-V3-0324")
    assert (
        _latency_swap_model("deepseek-ai/DeepSeek-V4-Flash")
        == "deepseek-ai/DeepSeek-V3-0324"
    )


def test_env_extends_thinking_prefixes(monkeypatch):
    _set(monkeypatch, NETMIND, "somelab/CoT-9000")
    monkeypatch.setenv("HELPER_REASONING_MODEL_PREFIXES", "somelab/CoT")
    assert _latency_swap_model("somelab/CoT-9000") == "deepseek-ai/DeepSeek-V3.2"


def test_env_extends_hosts(monkeypatch):
    _set(monkeypatch, "https://my-vllm.internal:8000/v1", "deepseek-ai/DeepSeek-V4-Flash")
    monkeypatch.setenv("HELPER_FAST_MODEL_HOSTS", "my-vllm.internal")
    assert _latency_swap_model("deepseek-ai/DeepSeek-V4-Flash") == "deepseek-ai/DeepSeek-V3.2"


def test_prefix_match_is_case_insensitive(monkeypatch):
    _set(monkeypatch, NETMIND, "DeepSeek-AI/deepseek-v4-flash")
    assert _latency_swap_model("DeepSeek-AI/deepseek-v4-flash") == "deepseek-ai/DeepSeek-V3.2"


def test_all_three_helper_sdks_accept_latency_sensitive():
    # Interface-parity contract: narrative call sites pass
    # latency_sensitive=True without knowing which protocol resolved;
    # every llm_function must accept it (anthropic/cli as a no-op).
    from xyz_agent_context.agent_framework.llm.anthropic_helper import AnthropicHelperSDK
    from xyz_agent_context.agent_framework.llm.cli_helper import CliHelperSDK

    for cls in (oa.OpenAIAgentsSDK, AnthropicHelperSDK, CliHelperSDK):
        params = inspect.signature(cls.llm_function).parameters
        assert "latency_sensitive" in params, cls.__name__
        assert params["latency_sensitive"].default is False, cls.__name__


def test_narrative_preflight_call_sites_are_latency_sensitive():
    # The three selection-preflight call sites (continuity detect, unified
    # judge, single-match confirm) must all opt in — they are the setup_s
    # hot path measured in [turn-timing].
    import xyz_agent_context.narrative._narrative_impl._retrieval_llm as retrieval_llm
    import xyz_agent_context.narrative._narrative_impl.continuity as continuity

    for module, fn in (
        (retrieval_llm, "llm_confirm"),
        (retrieval_llm, "llm_judge_unified"),
        (continuity, None),
    ):
        source = inspect.getsource(getattr(module, fn) if fn else module)
        assert "latency_sensitive=True" in source, (module.__name__, fn)
