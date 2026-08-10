from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest


class _FakeDb:
    def __init__(self):
        self.rows = []

    async def get_one(self, table, filters):
        return next(
            (
                row for row in self.rows
                if table == "product_analytics_events"
                and row["analytics_event_id"] == filters["analytics_event_id"]
            ),
            None,
        )

    async def insert(self, table, row):
        assert table == "product_analytics_events"
        self.rows.append(row)


@pytest.mark.asyncio
async def test_track_persists_indexed_first_party_dimensions(monkeypatch):
    import xyz_agent_context.analytics as analytics

    db = _FakeDb()
    monkeypatch.setattr(analytics, "_opted_out", AsyncMock(return_value=False))
    monkeypatch.setattr(
        "xyz_agent_context.utils.get_db_client", AsyncMock(return_value=db)
    )

    await analytics.track(
        user_id="user-1",
        event="message_accepted",
        event_id="event-fixed-1",
        properties={
            "source": "backend",
            "run_id": "run-1",
            "trigger_source": "chat",
            "latency_ms": 123,
        },
    )
    await analytics.track(
        user_id="user-1",
        event="message_accepted",
        event_id="event-fixed-1",
    )

    assert len(db.rows) == 1
    assert db.rows[0]["user_id"] == "user-1"
    assert db.rows[0]["event_name"] == "message_accepted"
    assert db.rows[0]["run_id"] == "run-1"
    assert db.rows[0]["trigger_source"] == "chat"
    assert db.rows[0]["latency_ms"] == 123


@pytest.mark.asyncio
async def test_frontend_route_derives_identity_and_rejects_unknown_event(monkeypatch):
    from fastapi import HTTPException
    from backend.routes import analytics as route

    capture = AsyncMock()
    monkeypatch.setattr(route, "track", capture)
    monkeypatch.setattr(
        route, "resolve_current_user_id", AsyncMock(return_value="user-1")
    )
    request = SimpleNamespace(state=SimpleNamespace(user_id="user-1"))

    response = await route.capture_product_event(
        route.ProductEventRequest(
            event="reply_rendered",
            event_id="event-fixed-2",
            run_id="run-2",
            agent_id="agent-1",
        ),
        request,
    )
    assert response == {"success": True}
    assert capture.await_args.kwargs["user_id"] == "user-1"
    assert capture.await_args.kwargs["properties"]["source"] == "frontend"

    with pytest.raises(HTTPException) as exc:
        await route.capture_product_event(
            route.ProductEventRequest(event="arbitrary", event_id="event-fixed-3"),
            request,
        )
    assert exc.value.status_code == 400


def test_schema_registers_product_events_and_exact_provider_source():
    from xyz_agent_context.utils.db.schema_registry import TABLES

    product = TABLES["product_analytics_events"]
    assert {column.name for column in product.columns} >= {
        "analytics_event_id", "event_name", "user_id", "failure_category",
        "provider_card_source", "occurred_at",
    }
    cost = TABLES["cost_records"]
    assert "provider_card_source" in {column.name for column in cost.columns}


def test_provider_card_source_is_selected_by_call_type():
    from xyz_agent_context.agent_framework.api_config import (
        get_provider_card_source,
        set_provider_card_sources,
    )

    set_provider_card_sources({
        "agent": "netmind_free",
        "helper_llm": "openai",
    })
    assert get_provider_card_source("agent_loop") == "netmind_free"
    assert get_provider_card_source("llm_function") == "openai"


def test_message_failure_categories_are_normalized():
    from xyz_agent_context.agent_runtime.background_run import _failure_category
    from xyz_agent_context.agent_runtime.run_recorder import (
        STATE_CANCELLED,
        STATE_COMPLETED,
    )

    assert _failure_category("free_tier_exhausted", STATE_COMPLETED) == "quota"
    assert _failure_category("invalid_credentials", STATE_COMPLETED) == "auth"
    assert _failure_category("context_window", STATE_COMPLETED) == "configuration"
    assert _failure_category("executor_oom", STATE_COMPLETED) == "infrastructure"
    assert _failure_category("anything_else", STATE_COMPLETED) == "runtime"
    assert _failure_category(None, STATE_CANCELLED) == "cancelled"
