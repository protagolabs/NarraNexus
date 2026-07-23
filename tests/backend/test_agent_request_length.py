"""
@file_name: test_agent_request_length.py
@author: NetMind.AI
@date: 2026-07-23
@description: Write-edge length validation for agent name/description.

The Create/Update agent request models cap agent_name and agent_description at
AGENT_TEXT_MAX_LENGTH so an over-long value is rejected at the API boundary
(422) instead of being written raw and only failing later on read. Companion
to the import-side trimming (tests/bundle/test_agent_field_length.py) — both
close the #71 gap where the write path bypassed the read-model's ceiling.
"""

import pytest
from pydantic import ValidationError

from xyz_agent_context.schema.entity_schema import AGENT_TEXT_MAX_LENGTH
from xyz_agent_context.schema.api_schema import CreateAgentRequest, UpdateAgentRequest

OVER = "x" * (AGENT_TEXT_MAX_LENGTH + 1)
AT_LIMIT = "y" * AGENT_TEXT_MAX_LENGTH


@pytest.mark.parametrize("model", [CreateAgentRequest, UpdateAgentRequest])
@pytest.mark.parametrize("field", ["agent_name", "agent_description"])
def test_overlong_rejected(model, field):
    with pytest.raises(ValidationError):
        model(**{field: OVER})


@pytest.mark.parametrize("model", [CreateAgentRequest, UpdateAgentRequest])
@pytest.mark.parametrize("field", ["agent_name", "agent_description"])
def test_at_limit_accepted(model, field):
    obj = model(**{field: AT_LIMIT})
    assert getattr(obj, field) == AT_LIMIT


@pytest.mark.parametrize("model", [CreateAgentRequest, UpdateAgentRequest])
def test_none_accepted(model):
    # Both fields are optional — omitting them stays valid.
    obj = model()
    assert obj.agent_name is None
    assert obj.agent_description is None
