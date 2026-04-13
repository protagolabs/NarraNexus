"""
@file_name: test_dashboard_schema.py
@description: T10 — discriminated union correctness.
"""
import pytest
from pydantic import TypeAdapter, ValidationError

from backend.routes._dashboard_schema import (
    AgentStatus,
    OwnedAgentStatus,
    PublicAgentStatus,
)


def test_public_serializes_as_public_variant():
    data = {
        "agent_id": "a1",
        "name": "A",
        "description": None,
        "is_public": True,
        "owned_by_viewer": False,
        "status": {"kind": "idle", "last_activity_at": None, "started_at": None},
        "running_count_bucket": "0",
    }
    obj = TypeAdapter(AgentStatus).validate_python(data)
    assert isinstance(obj, PublicAgentStatus)


def test_owned_serializes_as_owned_variant():
    data = {
        "agent_id": "a1",
        "name": "A",
        "description": None,
        "is_public": False,
        "owned_by_viewer": True,
        "status": {"kind": "idle", "last_activity_at": None, "started_at": None},
        "running_count": 0,
        "action_line": None,
        "sessions": [],
        "running_jobs": [],
        "pending_jobs": [],
        "enhanced": {
            "recent_errors_1h": 0,
            "token_rate_1h": None,
            "active_narratives": 0,
            "unread_bus_messages": 0,
        },
    }
    obj = TypeAdapter(AgentStatus).validate_python(data)
    assert isinstance(obj, OwnedAgentStatus)


def test_public_rejects_owner_only_field():
    data = {
        "agent_id": "a1",
        "name": "A",
        "description": None,
        "is_public": True,
        "owned_by_viewer": False,
        "status": {"kind": "idle", "last_activity_at": None, "started_at": None},
        "running_count_bucket": "0",
        "running_count": 42,  # should be rejected by extra='forbid'
    }
    with pytest.raises(ValidationError):
        TypeAdapter(AgentStatus).validate_python(data)


def test_public_dump_has_exact_field_whitelist():
    obj = PublicAgentStatus(
        agent_id="a1",
        name="A",
        status={"kind": "idle", "last_activity_at": None, "started_at": None},
        running_count_bucket="0",
    )
    dumped = obj.model_dump()
    expected = {"agent_id", "name", "description", "is_public", "owned_by_viewer",
                "status", "running_count_bucket"}
    assert set(dumped.keys()) == expected
    assert set(dumped["status"].keys()) == {"kind", "last_activity_at", "started_at"}
