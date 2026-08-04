"""
@file_name: test_mcp_identity_injection.py
@author: NarraNexus
@date: 2026-08-01
@description: ContextRuntime must tell each module MCP server WHICH agent is
calling (P1 evt_0dcee899, injection half).

``module/_mcp_identity.py`` can only correct a tool's ``agent_id`` if the
identity actually reaches the server, and there is exactly one place that
decides: the per-agent ``mcp_servers`` spec built in
``build_input_for_framework``. Both CLI adapters consume that same dict, so
if the headers are missing here the whole fix silently degrades to the old
"trust the model" behaviour — with no error anywhere. Hence this test.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from xyz_agent_context.module._mcp_identity import (
    AGENT_ID_HEADER,
    BEARER_AGENT_PREFIX,
)

AGENT = "agent_d8795abf5021"


class _FakeModule:
    """A module that only needs to answer get_mcp_config()."""

    def __init__(self, name: str, url: str):
        self._name, self._url = name, url

    async def get_mcp_config(self):
        return SimpleNamespace(server_name=self._name, server_url=self._url)

    async def get_turn_context(self, ctx_data):
        return ""

    async def get_expressive_tools(self, ctx_data=None):
        # Accepts ctx_data since #228 — context_runtime calls it WITH the
        # arg and logs "declaration DROPPED" (error) on signature drift, so a
        # stale fake here would silently exercise that error path.
        return []


def _instance(module_class: str, module) -> SimpleNamespace:
    return SimpleNamespace(
        module_class=module_class, instance_id=f"{module_class}_1", module=module
    )


@pytest.mark.asyncio
async def test_mcp_spec_carries_the_callers_identity():
    from xyz_agent_context.context_runtime.context_runtime import ContextRuntime
    from xyz_agent_context.schema import ContextData

    runtime = ContextRuntime(agent_id=AGENT, user_id="user_tc",
                             database_client=object())
    instances = [
        _instance("SocialNetworkModule", _FakeModule("social_network_module",
                                                    "http://127.0.0.1:7802/sse")),
        _instance("MessageBusModule", _FakeModule("message_bus_module",
                                                 "http://127.0.0.1:7810/sse")),
    ]

    # returns (messages, mcp_servers, disallowed_tools, expressive_tools)
    _msgs, mcp_servers, *_rest = await runtime.build_input_for_framework(
        messages=[],
        system_prompt="sys",
        active_instances=instances,
        ctx_data=ContextData(agent_id=AGENT, input_content="hi"),
    )

    assert set(mcp_servers) == {"social_network_module", "message_bus_module"}
    for name, spec in mcp_servers.items():
        headers = spec.get("headers") or {}
        # The explicit header (claude adapter forwards arbitrary headers)...
        assert headers.get(AGENT_ID_HEADER) == AGENT, f"{name} lost the identity header"
        # ...and the borrowed bearer (the only shape codex can transmit).
        assert headers.get("Authorization") == f"Bearer {BEARER_AGENT_PREFIX}{AGENT}"
        # The URL must be untouched — identity rides headers, not the query
        # string (a query param is LOST on the SSE transport).
        assert "?" not in spec["url"]


@pytest.mark.asyncio
async def test_injected_identity_is_this_agent_not_a_constant():
    """Guard against a hardcoded/stale id: two runtimes must inject their own."""
    from xyz_agent_context.context_runtime.context_runtime import ContextRuntime
    from xyz_agent_context.schema import ContextData

    seen = {}
    for agent in (AGENT, "agent_25dec880a426"):
        runtime = ContextRuntime(agent_id=agent, user_id="user_tc",
                                 database_client=object())
        _m, servers, *_r = await runtime.build_input_for_framework(
            messages=[],
            system_prompt="sys",
            active_instances=[
                _instance("SocialNetworkModule",
                          _FakeModule("social_network_module", "http://x/sse"))
            ],
            ctx_data=ContextData(agent_id=AGENT, input_content="hi"),
        )
        seen[agent] = servers["social_network_module"]["headers"][AGENT_ID_HEADER]

    assert seen == {AGENT: AGENT, "agent_25dec880a426": "agent_25dec880a426"}


@pytest.mark.asyncio
async def test_errand_continuation_bus_turn_upgrades_the_turn_source_stamp():
    """A MESSAGE_BUS turn that continues the agent's OWN errand must stamp
    its bus sends BUS_ERRAND_TURN_SOURCE, not the plain "message_bus" — the
    plain stamp makes a clarifying follow-up look like an ANSWER to the
    recipient's classifier and Owner Relay fires on the wrong side (P1
    recurrence, 2026-08-03 review). The flag arrives from MessageBusTrigger
    via trigger_extra_data → ctx_data.extra_data; a plain bus turn (answering
    a peer) must keep "message_bus".
    """
    from xyz_agent_context.context_runtime.context_runtime import ContextRuntime
    from xyz_agent_context.module._mcp_identity import BEARER_FIELD_SEP
    from xyz_agent_context.schema import (
        BUS_ERRAND_TURN_SOURCE,
        ContextData,
        WorkingSource,
    )

    async def _headers(extra_data):
        runtime = ContextRuntime(agent_id=AGENT, user_id="user_tc",
                                 database_client=object())
        ctx = ContextData(agent_id=AGENT, input_content="hi")
        ctx.working_source = WorkingSource.MESSAGE_BUS
        ctx.extra_data = extra_data
        _m, servers, *_r = await runtime.build_input_for_framework(
            messages=[],
            system_prompt="sys",
            active_instances=[
                _instance("MessageBusModule",
                          _FakeModule("message_bus_module", "http://x/sse"))
            ],
            ctx_data=ctx,
        )
        return servers["message_bus_module"]["headers"]

    asking = await _headers({"bus_turn_is_errand_continuation": True})
    answering = await _headers({"bus_turn_is_errand_continuation": False})

    # Both channels must carry the upgraded stamp — codex only forwards the
    # bearer, so a header-only upgrade would leave codex askers unfixed.
    expect_ask = f"Bearer {BEARER_AGENT_PREFIX}{AGENT}{BEARER_FIELD_SEP}{BUS_ERRAND_TURN_SOURCE}"
    assert asking["Authorization"] == expect_ask
    assert asking["X-NarraNexus-Turn-Source"] == BUS_ERRAND_TURN_SOURCE

    expect_answer = f"Bearer {BEARER_AGENT_PREFIX}{AGENT}{BEARER_FIELD_SEP}message_bus"
    assert answering["Authorization"] == expect_answer
    assert answering["X-NarraNexus-Turn-Source"] == "message_bus"
