"""
@file_name: test_kind_contracts.py
@author: Bin Liang
@date: 2026-09-03
@description: The kind contracts are the canonical definitions and the builtin implementations satisfy them.
"""
from __future__ import annotations

import re

import pytest

from narranexus.contracts import API_VERSIONS
from narranexus.contracts.events import HOST_EVENTS
from narranexus.contracts.framework import CAPABILITY_VOCABULARY, AgentLoopDriver, FrameworkMeta, InstallSpec
from narranexus.contracts.llm_client import LlmClient
from narranexus.contracts.memory import MemoryKindContract
from narranexus.contracts.provider import ProviderDriver
from narranexus.contracts.testing.framework import FrameworkDriverContractTests
from narranexus.contracts.testing.memory import MemoryKindContractTests
from narranexus.contracts.testing.provider import ProviderDriverContractTests


def test_capability_vocabulary_is_the_planned_set():
    assert CAPABILITY_VOCABULARY == frozenset(
        {
            "steering", "plan", "resume", "fork", "sleep", "subagent_announce",
            "event_log", "interrupt_soft", "raw_context", "arg_streaming",
        }
    )


def test_every_kind_contract_has_a_version():
    assert {"framework", "provider", "llm_client", "memory", "events"} <= set(API_VERSIONS)


def test_legacy_driver_module_re_exports_the_contract_protocol():
    from xyz_agent_context.agent_framework.loop import driver

    assert driver.AgentLoopDriver is AgentLoopDriver


def test_framework_meta_is_frozen():
    meta = FrameworkMeta(name="x", display_name="X", install=InstallSpec(kind="pip", requirement="x>=1"))
    with pytest.raises(Exception):
        meta.name = "y"  # type: ignore[misc]


def test_helper_sdks_satisfy_llm_client_contract():
    from xyz_agent_context.agent_framework.llm.anthropic_helper import AnthropicHelperSDK
    from xyz_agent_context.agent_framework.llm.cli_helper import CliHelperSDK
    from xyz_agent_context.agent_framework.adapters.openai_agents import OpenAIAgentsSDK

    for cls in (AnthropicHelperSDK, CliHelperSDK, OpenAIAgentsSDK):
        assert all(hasattr(cls, m) for m in ("llm_function", "llm_stream")), cls
        assert issubclass(cls, object) and isinstance(LlmClient, type)


def test_host_event_names_follow_on_did_or_on_will_verb_subject():
    for name in HOST_EVENTS:
        assert re.match(r"^on(Did|Will)[A-Z][A-Za-z]+$", name), name
    assert len(set(HOST_EVENTS)) == len(HOST_EVENTS)


class TestNexusPowerDriverContract(FrameworkDriverContractTests):
    @staticmethod
    def driver_factory():
        from xyz_agent_context.agent_framework.adapters.nexus.nexus_agent import NexusAgent

        return NexusAgent()


class TestRemoteDriverContract(FrameworkDriverContractTests):
    @staticmethod
    def driver_factory():
        from xyz_agent_context.agent_framework.loop.remote_driver import RemoteAgentLoopDriver

        return RemoteAgentLoopDriver(framework="nexus_power", executor_url="http://127.0.0.1:1", working_path=".")


def _provider_driver_classes() -> list[type]:
    import xyz_agent_context.agent_framework.providers.driver.drivers  # noqa: F401 registers
    from xyz_agent_context.agent_framework.providers.driver.registry import get_driver_class

    keys = [
        "custom_anthropic", "custom_openai", "netmind", "netmind_free", "yunwu",
        "openrouter", "claude_oauth", "codex_oauth",
    ]
    classes = [get_driver_class(k) for k in keys]
    assert all(classes), "a builtin provider driver failed to register"
    return classes  # type: ignore[return-value]


@pytest.mark.parametrize("driver_cls", _provider_driver_classes(), ids=lambda c: c.driver_type())
class TestBuiltinProviderDriversSatisfyContract:
    def test_structural(self, driver_cls):
        for name in ("driver_type", "build_claude_config", "build_openai_config",
                     "build_anthropic_helper_config", "build_cli_helper_config",
                     "build_codex_config", "probe", "models"):
            assert hasattr(driver_cls, name), name
        assert isinstance(ProviderDriver, type)


class TestNetmindProviderContract(ProviderDriverContractTests):
    driver_cls = _provider_driver_classes()[2]  # netmind


def _memory_specs():
    import xyz_agent_context.memory.specs  # noqa: F401 registers
    from xyz_agent_context.memory.spec import all_kinds, get_spec

    return [get_spec(k) for k in sorted(all_kinds())]


@pytest.mark.parametrize("spec", _memory_specs(), ids=lambda s: s.kind)
def test_builtin_memory_kinds_satisfy_contract(spec):
    assert isinstance(spec, MemoryKindContract)


class TestEventMemoryKindContract(MemoryKindContractTests):
    @staticmethod
    def spec_factory():
        import xyz_agent_context.memory.specs  # noqa: F401
        from xyz_agent_context.memory.spec import get_spec

        return get_spec("event")
