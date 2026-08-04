"""
@file_name: test_unset_description_surfaces.py
@author: NarraNexus
@date: 2026-08-04
@description: An agent with no description says NOTHING about it — to peers and
to itself.

P1 段02: the creation placeholder ("A new agent ready for configuration") was
never replaced and reached the model on three surfaces. Two of them are
rendered here, and both were actively harmful rather than merely useless:

  * the Known Agents list injected into every bus turn showed that line beside
    EVERY peer, so an agent told to "ask the teaching expert" read a roster in
    which no entry claimed to be anything;
  * BasicInfo injects the same field as the agent's OWN self-description, so
    the agent read that it was a new agent awaiting configuration — and said
    so when a peer asked whether it was set up (evt_feb1f6ae).

(The third surface, ``bus_agent_registry``, is covered by
tests/services/test_agent_discovery_sync.py.)
"""
from __future__ import annotations

import pytest

from xyz_agent_context.schema import (
    LEGACY_AGENT_DESCRIPTION_PLACEHOLDER,
    ContextData,
)

PLACEHOLDER = LEGACY_AGENT_DESCRIPTION_PLACEHOLDER


# ---------------------------------------------------------------------------
# Known Agents (peer roster)
# ---------------------------------------------------------------------------


def _roster(known: list[dict]) -> str:
    from xyz_agent_context.module.message_bus_module.message_bus_module import (
        MessageBusModule,
    )

    module = MessageBusModule(agent_id="agent_me", user_id="user_tc",
                              database_client=object())
    ctx = ContextData(agent_id="agent_me", input_content="hi")
    ctx.extra_data = {"bus_known_agents": known}
    return "\n".join(str(p) for p in module._volatile_context_parts(ctx))


@pytest.mark.parametrize("description", [PLACEHOLDER, "", None])
def test_a_peer_without_a_description_is_listed_by_name_only(description):
    out = _roster([
        {"agent_id": "agent_peer", "agent_name": "咕咕嘎嘎",
         "agent_description": description},
    ])

    assert "agent_peer" in out and "咕咕嘎嘎" in out
    assert "ready for configuration" not in out.lower()
    # No dangling separator either — the line must simply end after the name.
    assert "咕咕嘎嘎:" not in out


def test_a_real_peer_description_is_shown():
    out = _roster([
        {"agent_id": "agent_peer", "agent_name": "咕咕嘎嘎",
         "agent_description": "Reviews lesson plans."},
    ])

    assert "Reviews lesson plans." in out


def test_one_configured_peer_stays_distinguishable_among_blank_ones():
    """The point of the fix: the roster must let the asking agent tell peers
    apart. With the placeholder printed everywhere, every line looked alike."""
    out = _roster([
        {"agent_id": "agent_a", "agent_name": "A", "agent_description": PLACEHOLDER},
        {"agent_id": "agent_b", "agent_name": "B",
         "agent_description": "Teaching expert: curriculum and lesson review."},
        {"agent_id": "agent_c", "agent_name": "C", "agent_description": ""},
    ])

    assert out.count("Teaching expert") == 1
    assert "ready for configuration" not in out.lower()


# ---------------------------------------------------------------------------
# BasicInfo (the agent's own self-description)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize("stored", [PLACEHOLDER, "", None])
async def test_the_agent_reads_an_instruction_not_a_false_status(stored, monkeypatch):
    from xyz_agent_context.module.basic_info_module import basic_info_module as mod

    ctx = await _gather_basic_info(mod, monkeypatch, stored)

    assert ctx.agent_description == mod.UNSET_AGENT_DESCRIPTION_NOTICE
    assert "ready for configuration" not in ctx.agent_description.lower()
    # It must point at the fix, so an agent that notices can act in-turn.
    assert "update_agent_profile" in ctx.agent_description


@pytest.mark.asyncio
async def test_a_real_self_description_is_passed_through_verbatim(monkeypatch):
    from xyz_agent_context.module.basic_info_module import basic_info_module as mod

    ctx = await _gather_basic_info(mod, monkeypatch, "Reviews lesson plans.")

    assert ctx.agent_description == "Reviews lesson plans."


async def _gather_basic_info(mod, monkeypatch, stored_description):
    """Run BasicInfoModule.hook_data_gathering against a stubbed agent row."""
    from types import SimpleNamespace

    class _FakeAgentRepo:
        def __init__(self, _db):
            pass

        async def get_agent(self, agent_id):
            return SimpleNamespace(
                agent_id=agent_id, agent_name="咕咕嘎嘎",
                agent_description=stored_description, created_by="user_tc",
                agent_create_time=None, is_public=False,
            )

    class _FakeUserRepo:
        def __init__(self, _db):
            pass

        async def get_display_name(self, user_id):
            return "TC"

    import xyz_agent_context.repository as repo_pkg
    monkeypatch.setattr(repo_pkg, "AgentRepository", _FakeAgentRepo)
    monkeypatch.setattr(repo_pkg, "UserRepository", _FakeUserRepo)
    monkeypatch.setattr(mod, "AgentRepository", _FakeAgentRepo, raising=False)

    module = mod.BasicInfoModule(agent_id="agent_me", user_id="user_tc",
                                 database_client=object())
    ctx = ContextData(agent_id="agent_me", input_content="hi")
    return await module.hook_data_gathering(ctx)
