"""
@file_name: _telegram_hooks.py
@author: NarraNexus
@date: 2026-03-29
@description: TelegramModule hook implementations

hook_data_gathering:
  - Inject Telegram bot identity info (bot_username, bot_id, is_active)

hook_after_event_execution:
  - Placeholder for post-execution cleanup (Telegram has no mark_read equivalent)
  - Logs for observability
"""

from __future__ import annotations

from loguru import logger

from xyz_agent_context.schema import ContextData, HookAfterExecutionParams
from xyz_agent_context.utils import DatabaseClient

from ._telegram_credential_manager import TelegramCredentialManager


async def telegram_hook_data_gathering(
    agent_id: str,
    db: DatabaseClient,
    ctx_data: ContextData,
) -> ContextData:
    """
    Inject Telegram-specific context into ctx_data.

    Steps:
    1. Check if Agent has an active Telegram credential
    2. If yes, inject telegram_info into ctx_data.extra_data

    Args:
        agent_id: Current Agent ID
        db: Database client
        ctx_data: Context data to enrich

    Returns:
        Enriched ContextData
    """
    cred_mgr = TelegramCredentialManager(db)
    cred = await cred_mgr.get_credential(agent_id)

    if not cred or not cred.is_active:
        logger.debug(f"Agent {agent_id} has no active Telegram credentials, skipping")
        return ctx_data

    ctx_data.extra_data["telegram_info"] = {
        "bot_username": cred.bot_username,
        "bot_id": cred.bot_id,
        "is_active": cred.is_active,
    }

    return ctx_data


async def telegram_hook_after_event_execution(
    params: HookAfterExecutionParams,
    db: DatabaseClient,
) -> None:
    """
    Post-execution cleanup for Telegram.

    V1 placeholder: Telegram Bot API has no mark_read equivalent.
    This hook logs the execution for observability and can be extended later
    (e.g., sending typing indicators, updating read state via webhooks).

    Args:
        params: Hook execution parameters
        db: Database client
    """
    ws = params.working_source
    ws_value = ws.value if hasattr(ws, "value") else str(ws)
    if ws_value != "telegram":
        return

    agent_id = params.agent_id
    logger.debug(f"Telegram hook_after_event_execution for agent {agent_id} (no-op in V1)")
