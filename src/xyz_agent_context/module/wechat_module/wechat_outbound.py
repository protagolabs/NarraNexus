"""
@file_name: wechat_outbound.py
@author:
@date: 2026-08-10
@description: Single decision point for WeChat outbound routing.

Every WeChat send site — the ``wechat_send`` MCP tool, the module's
``send_to_agent`` (ChannelSenderRegistry: step_3 DM fallback +
``contact_agent``), and the trigger's ``send_channel_reply`` (managed
error fallback) — calls ``send_wechat_text`` instead of ``send_text_once``
directly. The router decides per call:

- provider declared managed AND manyfold runtime env resolves →
  ``POST channel-send`` on the platform. No ``context_token`` needed:
  the platform resolves the recipient from ``room_id`` (= the WeChat peer
  id) and holds the iLink credential itself.
- otherwise → the legacy direct iLink path, byte-identical to before.

A failed platform send is returned as ``False`` and deliberately NOT
retried via direct iLink: managed mode means the platform owns delivery,
and a sandbox-side direct send would race the platform's own retry into a
double message.

One wrapper rather than a flag inside ``send_text_once`` because the SDK
client layer is transport-only and must not learn manyfold semantics;
routing is a module-layer concern.
"""
from __future__ import annotations

from typing import Any

from xyz_agent_context.utils.manyfold_outbound import (
    channel_send,
    managed_channel_send_active,
)

from .wechat_sdk_client import send_text_once


async def send_wechat_text(
    *,
    agent_id: str,
    credential: Any,
    to_user_id: str,
    context_token: str,
    text: str,
) -> bool:
    """Send one WeChat text, routed managed-vs-direct. Never raises."""
    if managed_channel_send_active("wechat"):
        return await channel_send(
            agent_id=agent_id,
            provider="wechat",
            room_id=to_user_id,
            text=text,
        )
    return await send_text_once(
        credential.bot_token, credential.base_url, to_user_id, context_token, text
    )
