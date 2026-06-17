"""
@file_name: broker_client.py
@author:
@date: 2026-06-17
@description: Orchestrator-side client for the per-user Executor Broker.

In cloud, the agent-loop runs in a per-user Executor container that the
broker spawns (only that user's workspace mounted, no platform secrets).
The executor URL is therefore DYNAMIC per user — this resolves it by
asking the broker to ensure the user's executor is up, returning its URL.

Gated on ``BROKER_URL`` (only the cloud orchestrator sets it). When unset
— local/desktop, or the older single static ``AGENT_EXECUTOR_URL`` model
— this returns ``None`` and the caller falls back (in-process driver, or
the static executor URL). So the integration is additive and backward
compatible.

This is the cold-start trigger point: ``ensure`` may spin up a container
(seconds), which is why the timeout is generous and why the run-start
flow surfaces a "waking up" state to the user (see handoff doc).
"""
from __future__ import annotations

import os
from typing import Optional

import httpx
from loguru import logger


def broker_url() -> Optional[str]:
    return (os.getenv("BROKER_URL") or "").strip() or None


async def resolve_executor_url(user_id: str, *, timeout: float = 120.0) -> Optional[str]:
    """Ensure this user's executor via the broker and return its URL.

    Returns ``None`` when no broker is configured (caller falls back).
    Raises on broker/transport error — in cloud we must NOT silently fall
    back to an in-process spawn (that would defeat isolation); the run
    fails loudly instead.
    """
    base = broker_url()
    if not base:
        return None
    endpoint = f"{base.rstrip('/')}/executors"
    async with httpx.AsyncClient(timeout=timeout) as client:
        resp = await client.post(endpoint, json={"user_id": user_id})
        resp.raise_for_status()
        data = resp.json()
    executor_url = data.get("executor_url")
    logger.info(
        f"[broker] ensured executor user={user_id} status={data.get('status')} url={executor_url}"
    )
    if not executor_url:
        raise RuntimeError(f"broker returned no executor_url for user {user_id!r}: {data}")
    return executor_url
