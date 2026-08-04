"""
@file_name: test_job_mcp_tool_hardening.py
@author: Bin Liang
@date: 2026-08-04
@description: job_create / job_update boundary hardening (W1).

Two live-reproduced defects:
1. job_create was the ONLY tool in the file with no try/except around its
   body — setup_mcp_llm_context raising LLMConfigNotConfigured reached the
   model as a raw exception string it read as "impossible", instead of a
   structured error telling it how to self-correct.
2. trigger_config was a bare `dict` — the model got zero structure hints
   (everything rode on docstring prose), and a stringified JSON payload
   crashed deep in the service as "argument of type 'str' is not a mapping".
   A typed shape (inline properties, no $ref, no anyOf-null) documents the
   fields in the schema itself and rejects malformed input at the boundary
   with a message the model can act on.
"""
from unittest.mock import AsyncMock, patch

import pytest

from xyz_agent_context.agent_framework.api_config import LLMConfigNotConfigured
from xyz_agent_context.module.job_module._job_mcp_tools import create_job_mcp_server

TOOLS_MOD = "xyz_agent_context.module.job_module._job_mcp_tools"


def _server(db_client=None):
    async def get_db():
        return db_client

    return create_job_mcp_server(port=0, get_db_client_fn=get_db)


def _tool(mcp, name):
    return {t.name: t for t in mcp._tool_manager.list_tools()}[name]


# ---------------------------------------------------------------------------
# trigger_config schema shape
# ---------------------------------------------------------------------------

def _trigger_schema(tool):
    props = tool.parameters["properties"]
    schema = props["trigger_config"]
    # Optional[...] wrapping on job_update may produce anyOf — find the
    # object arm; job_create must be the object directly.
    if "anyOf" in schema:
        arms = [a for a in schema["anyOf"] if a.get("type") == "object" or "properties" in a or "$ref" in a]
        assert arms, f"no object arm in {schema}"
        schema = arms[0]
    if "$ref" in schema:
        ref = schema["$ref"].rsplit("/", 1)[-1]
        schema = tool.parameters["$defs"][ref]
    return schema


def test_job_create_trigger_config_declares_properties():
    tool = _tool(_server(), "job_create")
    schema = _trigger_schema(tool)
    assert "properties" in schema, f"bare object schema: {schema}"
    keys = set(schema["properties"])
    assert {"timezone", "run_at", "cron", "interval_seconds", "end_condition"} <= keys


def test_job_update_trigger_config_declares_properties():
    tool = _tool(_server(), "job_update")
    schema = _trigger_schema(tool)
    assert "properties" in schema, f"bare object schema: {schema}"
    assert "cron" in schema["properties"]


def test_trigger_config_optional_fields_have_plain_types():
    """NotRequired fields must be plain-typed (absent = unset), never
    anyOf[X, null] — the exact shape strict providers reject."""
    tool = _tool(_server(), "job_create")
    schema = _trigger_schema(tool)
    for name, prop in schema["properties"].items():
        assert "anyOf" not in prop, f"{name} is anyOf-typed: {prop}"
        assert prop.get("type"), f"{name} has no explicit type: {prop}"


# ---------------------------------------------------------------------------
# job_create structured failure instead of raw exceptions
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_llm_config_failure_returns_structured_error():
    fn = _tool(_server(), "job_create").fn
    with patch(
        f"{TOOLS_MOD}.setup_mcp_llm_context",
        AsyncMock(side_effect=LLMConfigNotConfigured("Cannot resolve LLM config for agent_id=agent_current")),
    ):
        result = await fn(
            agent_id="agent_current",
            user_id="user_1",
            title="T",
            description="d",
            job_type="one_off",
            trigger_config={"run_at": "2026-09-01T09:00:00", "timezone": "Asia/Shanghai"},
            payload="p",
        )
    assert isinstance(result, dict)
    assert result["success"] is False
    # The error must be actionable: point the model at its instructions.
    assert "instructions" in result["error"].lower()


@pytest.mark.asyncio
async def test_unexpected_exception_returns_structured_error():
    fn = _tool(_server(), "job_create").fn
    with patch(
        f"{TOOLS_MOD}.setup_mcp_llm_context",
        AsyncMock(side_effect=RuntimeError("boom")),
    ):
        result = await fn(
            agent_id="agent_1",
            user_id="user_1",
            title="T",
            description="d",
            job_type="one_off",
            trigger_config={"run_at": "2026-09-01T09:00:00", "timezone": "Asia/Shanghai"},
            payload="p",
        )
    assert result == {"success": False, "error": "boom"}
