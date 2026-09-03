"""
@file_name: test_capability_adapter.py
@author: Bin Liang
@date: 2026-09-03
@description: LegacyModuleAdapter maps a real module's lifecycle onto stage participations without changing behavior.
"""
from __future__ import annotations

import pytest

from narranexus.contracts.agent import Capability, CapabilityTier, Stage
from narranexus.contracts.agent.capability import STAGE_METHODS
from xyz_agent_context.module.capability_adapter import LegacyModuleAdapter, capability_meta_for


@pytest.fixture
def chat_module(db_client):
    from xyz_agent_context.module.chat_module.chat_module import ChatModule

    return ChatModule("agent_adapter_test", "user_adapter_test", db_client)


def test_adapter_is_a_capability_with_module_meta(chat_module):
    cap = LegacyModuleAdapter(chat_module)
    assert isinstance(cap, Capability)
    assert cap.meta == capability_meta_for(chat_module)
    assert cap.meta.tier is CapabilityTier.MODULE
    assert cap.meta.name == chat_module.get_config().name
    assert cap.meta.provides_chat_history is type(chat_module).provides_chat_history()


def test_participations_cover_the_module_stages_and_satisfy_the_protocol(chat_module):
    parts = LegacyModuleAdapter(chat_module).participations()
    assert set(parts) == {Stage.INGRESS, Stage.ASSEMBLE, Stage.COMMIT, Stage.REFLECT}
    for stage, participant in parts.items():
        for method in STAGE_METHODS[stage]:
            assert callable(getattr(participant, method)), (stage, method)


def test_ingress_claim_equals_owns_working_source(chat_module):
    ingress = LegacyModuleAdapter(chat_module).participations()[Stage.INGRESS]
    for source in ("chat", "job", "lark", "message_bus"):
        assert ingress.claims_source(source) == chat_module.owns_working_source(source)


async def test_assemble_participant_delegates_to_the_module(chat_module):
    from xyz_agent_context.schema import ContextData

    ctx = ContextData(agent_id=chat_module.agent_id, user_id=chat_module.user_id, input_content="hello")
    assemble = LegacyModuleAdapter(chat_module).participations()[Stage.ASSEMBLE]
    assert await assemble.contribute_instructions(ctx) == await chat_module.get_instructions(ctx)
    assert await assemble.contribute_turn_context(ctx) == await chat_module.get_turn_context(ctx)
    surface = await assemble.contribute_tools(ctx)
    mcp = await chat_module.get_mcp_config()
    assert (mcp is None) == (surface.mcp_servers == {})
    if mcp is not None:
        assert list(surface.mcp_servers) == [mcp.server_name]
    assert surface.expressive_tools == tuple(await chat_module.get_expressive_tools(ctx))
    assert surface.disallowed_tools == tuple(await chat_module.get_disallowed_tools(ctx))
