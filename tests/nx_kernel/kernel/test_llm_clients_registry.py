"""
@file_name: test_llm_clients_registry.py
@author: Bin Liang
@date: 2026-09-03
@description: The helper-LLM protocol map is the kernel model.clients registry and its builtin manifest reproduces it.
"""
from __future__ import annotations

import pytest

from narranexus.kernel.plugins.builtins import builtin_manifests
from narranexus.kernel.plugins.loader import load
from narranexus.kernel.plugins.registries import KERNEL_REGISTRIES, Registries


def test_registry_identity_and_builtin_order():
    from narranexus.platform.agent_framework.llm.helper_sdk import CONTRIBUTIONS, LLM_CLIENT_REGISTRY

    assert LLM_CLIENT_REGISTRY is KERNEL_REGISTRIES.registry_for("model.clients")
    assert LLM_CLIENT_REGISTRY.names() == ("anthropic", "openai", "cli")
    assert [c.name for c in CONTRIBUTIONS] == ["anthropic", "openai", "cli"]
    assert {LLM_CLIENT_REGISTRY.owner_of(n) for n in LLM_CLIENT_REGISTRY.names()} == {"builtin.llm_clients"}


def test_builtin_manifest_reproduces_the_registry_in_a_fresh_registries():
    regs = Registries()
    manifests = [m for m in builtin_manifests() if m.id == "builtin.llm_clients"]
    report = load(regs, manifests, role="mcp")
    assert report.errors == []
    assert regs.registry_for("model.clients").names() == ("anthropic", "openai", "cli")


def test_get_helper_sdk_dispatches_by_protocol_and_fails_loud_for_unknown(monkeypatch):
    from narranexus.platform.agent_framework.llm import helper_sdk

    monkeypatch.setattr(helper_sdk, "_resolved_helper_protocol", lambda: "openai")
    from narranexus.platform.agent_framework.adapters.openai_agents import OpenAIAgentsSDK

    assert isinstance(helper_sdk.get_helper_sdk(), OpenAIAgentsSDK)
    monkeypatch.setattr(helper_sdk, "_resolved_helper_protocol", lambda: "nope")
    with pytest.raises(ValueError, match="No helper SDK registered for protocol 'nope'"):
        helper_sdk.get_helper_sdk()
