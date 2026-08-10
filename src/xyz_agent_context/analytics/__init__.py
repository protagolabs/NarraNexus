"""
@file_name: __init__.py
@date: 2026-06-08
@description: First-party product analytics persistence.

track() is the only entry point capture sites use. It is async because the
opt-out lookup and event insert hit the database. Events never leave the
NarraNexus database for the current surface: cloud writes RDS; local and
desktop write their local SQLite database. There is deliberately no vendor
telemetry sink.
"""
from __future__ import annotations

import json
import uuid
from typing import Optional

from loguru import logger

from xyz_agent_context.analytics.surface import SURFACE

__all__ = ["track"]


async def _opted_out(user_id: str) -> bool:
    try:
        from xyz_agent_context.utils import get_db_client
        from xyz_agent_context.repository.user_settings_repository import (
            UserSettingsRepository,
        )
        repo = UserSettingsRepository(await get_db_client())
        return await repo.is_analytics_opted_out(user_id)
    except Exception as e:  # noqa: BLE001
        logger.warning(f"[analytics] opt-out lookup failed for {user_id}: {e}")
        return False  # default: tracking on


async def track(*, user_id: str, event: str,
                properties: Optional[dict] = None,
                event_id: Optional[str] = None) -> None:
    try:
        if not user_id:
            return
        if await _opted_out(user_id):
            return
        props = dict(properties or {})
        props.setdefault("surface", SURFACE)
    except Exception as e:  # noqa: BLE001
        logger.warning(f"[analytics] prepare {event} failed: {e}")
        return
    try:
        await _persist_product_event(
            user_id=user_id,
            event=event,
            event_id=event_id,
            properties=props,
        )
    except Exception as e:  # noqa: BLE001
        logger.warning(f"[analytics] persist {event} failed: {e}")


async def _persist_product_event(*, user_id: str, event: str,
                                 event_id: Optional[str],
                                 properties: dict) -> None:
    """Persist the first-party event used by narranexus-data.

    Known dimensions are duplicated into indexed columns; the JSON payload is
    retained for low-volume diagnostics. Event bodies must never contain user
    message text, credentials, email addresses, or other free-form PII.
    """
    from xyz_agent_context.utils import get_db_client

    analytics_event_id = event_id or str(uuid.uuid4())
    db = await get_db_client()
    if event_id and await db.get_one(
        "product_analytics_events", {"analytics_event_id": analytics_event_id}
    ):
        return

    def _string(key: str) -> Optional[str]:
        value = properties.get(key)
        return str(value) if value is not None and value != "" else None

    latency = properties.get("latency_ms")
    try:
        latency_ms = int(latency) if latency is not None else None
    except (TypeError, ValueError):
        latency_ms = None

    await db.insert("product_analytics_events", {
        "analytics_event_id": analytics_event_id,
        "event_name": event,
        "user_id": user_id,
        "source": _string("source") or "backend",
        "surface": _string("surface"),
        "session_id": _string("session_id"),
        "run_id": _string("run_id"),
        "agent_id": _string("agent_id"),
        "trigger_source": _string("trigger_source"),
        "provider_card_source": _string("provider_card_source"),
        "failure_category": _string("failure_category"),
        "failure_reason": _string("failure_reason"),
        "latency_ms": latency_ms,
        "properties_json": json.dumps(
            properties, ensure_ascii=False, separators=(",", ":"), default=str
        ),
    })
