"""
@file_name: test_agent_events.py
@author: Bin Liang
@date: 2026-09-03
@description: The agent-loop event contract lives in contracts and the legacy module re-exports the same objects; wire values are pinned.
"""
from __future__ import annotations

from narranexus.contracts import API_VERSIONS
from narranexus.contracts import agent_events as contract
from tests.snapshots._approval import approve
from xyz_agent_context.agent_framework.loop import events as legacy


def test_legacy_module_re_exports_every_contract_name():
    assert set(legacy.__all__) == set(contract.__all__)
    for name in contract.__all__:
        assert getattr(legacy, name) is getattr(contract, name), name


def test_wire_values_are_pinned():
    approve(
        "agent_events",
        {
            "families": [contract.TYPE_RAW_RESPONSE_EVENT, contract.TYPE_RUN_ITEM_STREAM_EVENT],
            "data_types": [
                contract.DATA_TYPE_TEXT_DELTA, contract.DATA_TYPE_DONE, contract.DATA_TYPE_ERROR,
                contract.DATA_TYPE_USAGE, contract.DATA_TYPE_REPLY_DELTA, contract.DATA_TYPE_RETRY,
            ],
            "done_superseded_key": contract.DATA_TYPE_DONE_SUPERSEDED_KEY,
            "item_types": [
                contract.ITEM_TYPE_THINKING, contract.ITEM_TYPE_TOOL_CALL,
                contract.ITEM_TYPE_TOOL_CALL_OUTPUT, contract.ITEM_TYPE_MESSAGE_OUTPUT, contract.ITEM_TYPE_PLAN,
            ],
            "cli_error_types": sorted(contract.CLI_ERROR_TYPES),
            "usage_cache_read_keys": list(contract.USAGE_CACHE_READ_KEYS),
            "usage_cache_creation_key": contract.USAGE_CACHE_CREATION_KEY,
            "api_version": API_VERSIONS["agent_events"],
        },
    )


def test_constructors_build_contract_shapes():
    ev = contract.raw_text_delta_event("hi")
    assert ev == {"type": "raw_response_event", "data": {"type": "response.text.delta", "delta": "hi"}}
    err = contract.raw_error_event("boom", "server_error")
    assert err["data"]["error_type"] in contract.CLI_ERROR_TYPES
