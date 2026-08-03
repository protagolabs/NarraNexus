"""
@file_name: managed_channel_ingress.py
@author: NexusAgent
@date: 2026-08-03
@description: Managed-mode trigger executor for platform-forwarded IM turns.

Under Manyfold managed mode the platform owns each channel's connection and
cleaning pipeline (dedup / echo filtering / mention gate) and forwards
inbound IM messages as chat turns to ``POST /v1/chat/completions``. That
endpoint maps the turn onto the channel's native semantics
(``build_inbound_run_context``) — but the native channel triggers also carry
per-message BUSINESS hooks that live on the receive path the platform now
holds: WeChat's first-DM owner claim, NarraMessenger's authorize-event gate,
the inbox write, and the error fallback send.

This coordinator is the managed-mode host for those hooks: it constructs one
trigger instance per channel from CHANNEL_TRIGGER_MAP (``start()`` is never
called — no subscribe loops, no connections) and routes
``managed_before_run`` / ``managed_after_run`` around the agent run. It is a
peer of ``run_channel_triggers`` (a coordinator over the trigger registry),
not a Module — modules stay independent (binding rule #3).

Design: reference/self_notebook/specs/2026-08-03-manyfold-managed-im-ingress-design.md §3.2/Q4/Q6.
"""

from __future__ import annotations

from typing import Any, Optional

from loguru import logger

from xyz_agent_context.channel.channel_trigger_base import ChannelTriggerBase
from xyz_agent_context.module.channel_trigger_map import CHANNEL_TRIGGER_MAP
from xyz_agent_context.schema.hook_schema import WorkingSource
from xyz_agent_context.schema.parsed_message import (
    ChatType,
    MessageContentType,
    ParsedMessage,
)


def synthesize_managed_message(
    trigger_extra_data: dict, user_input: str
) -> ParsedMessage:
    """Rebuild a minimal ParsedMessage from the managed-IM contract fields.

    Just enough for the trigger business hooks: claim / authorize / inbox /
    error fallback address their work via ``sender_id`` / ``chat_id`` /
    ``chat_type`` / ``raw`` (WeChat's reply routing reads
    ``raw["context_token"]`` — carried as ``reply_token`` on the wire).
    """
    tag = trigger_extra_data.get("channel_tag") or {}
    sender_id = str(tag.get("sender_id", "") or "")
    room_id = str(tag.get("room_id", "") or "")
    chat_type = (
        ChatType.GROUP
        if trigger_extra_data.get("chat_type") == "group"
        else ChatType.PRIVATE
    )
    return ParsedMessage(
        message_id=str(
            trigger_extra_data.get("source_message_id", "")
            or trigger_extra_data.get("trigger_id", "")
        ),
        chat_id=room_id,
        sender_id=sender_id,
        sender_name=str(tag.get("sender_name", "") or sender_id or "user"),
        content=user_input,
        content_type=MessageContentType.TEXT,
        chat_type=chat_type,
        thread_id=(str(trigger_extra_data.get("thread_id") or "") or None),
        raw={
            "managed_ingress": True,
            "from_user_id": sender_id,
            "context_token": str(trigger_extra_data.get("reply_token", "") or ""),
        },
    )


class ManagedChannelIngress:
    """Per-process host of start()-less trigger instances.

    Construction failures are isolated per channel (same defensive stance as
    ``run_channel_triggers``): a channel whose optional dependency is missing
    degrades that channel only. Failure semantics differ by hook kind —
    side-effect channels fail OPEN (the run proceeds; downstream surfaces
    no_credential etc.), while narramessenger fails CLOSED because its hook
    is authorization.
    """

    def __init__(self) -> None:
        self._triggers: dict[str, Optional[ChannelTriggerBase]] = {}

    def _trigger(self, channel: str) -> Optional[ChannelTriggerBase]:
        if channel in self._triggers:
            return self._triggers[channel]
        cls = CHANNEL_TRIGGER_MAP.get(channel)
        trigger: Optional[ChannelTriggerBase] = None
        if cls is None:
            logger.warning(
                f"managed ingress: no trigger class for channel {channel!r}"
            )
        else:
            try:
                trigger = cls()
            except Exception as e:  # noqa: BLE001 — per-channel isolation
                logger.warning(
                    f"managed ingress: constructing {cls.__name__} failed "
                    f"({type(e).__name__}: {e})"
                )
        self._triggers[channel] = trigger
        return trigger

    async def before_run(
        self,
        *,
        working_source: WorkingSource,
        agent_id: str,
        user_input: str,
        trigger_extra_data: dict,
        db: Any,
    ) -> tuple[bool, str]:
        """Run the channel's pre-run business gate. Returns (allow, receipt)."""
        channel = working_source.value
        trigger = self._trigger(channel)
        if trigger is None:
            if working_source is WorkingSource.NARRAMESSENGER:
                return False, (
                    "narramessenger authorization unavailable "
                    "(trigger not loadable)"
                )
            return True, ""
        message = synthesize_managed_message(trigger_extra_data, user_input)
        is_mention = bool(trigger_extra_data.get("is_mention", True))
        try:
            return await trigger.managed_before_run(
                agent_id=agent_id, message=message, db=db, is_mention=is_mention
            )
        except Exception as e:  # noqa: BLE001
            logger.warning(
                f"managed ingress before_run failed for {channel} "
                f"({type(e).__name__}: {e})"
            )
            if working_source is WorkingSource.NARRAMESSENGER:
                # The hook that failed IS the authorization gate: fail-closed.
                return False, "narramessenger authorization failed"
            return True, ""

    async def after_run(
        self,
        *,
        working_source: WorkingSource,
        agent_id: str,
        user_input: str,
        trigger_extra_data: dict,
        db: Any,
        reply_text: str,
        error_text: str = "",
    ) -> None:
        """Run the channel's post-run bookkeeping (inbox / audit / error
        fallback). Best-effort; never raises."""
        trigger = self._trigger(working_source.value)
        if trigger is None:
            return
        message = synthesize_managed_message(trigger_extra_data, user_input)
        try:
            await trigger.managed_after_run(
                agent_id=agent_id,
                message=message,
                db=db,
                reply_text=reply_text,
                error_text=error_text,
            )
        except Exception as e:  # noqa: BLE001
            logger.warning(
                f"managed ingress after_run failed for {working_source.value} "
                f"({type(e).__name__}: {e})"
            )


_INGRESS: Optional[ManagedChannelIngress] = None


def get_managed_channel_ingress() -> ManagedChannelIngress:
    """Process-wide singleton (trigger instances are cached per channel)."""
    global _INGRESS
    if _INGRESS is None:
        _INGRESS = ManagedChannelIngress()
    return _INGRESS
