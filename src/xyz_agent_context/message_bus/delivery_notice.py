"""
@file_name: delivery_notice.py
@author: NarraNexus
@date: 2026-08-13
@description: The room line the platform writes when a turn delivered nothing.

A bus turn ends in one of three ways, and until now only the first was visible:

1. it produced a reply, which was posted — the room shows it;
2. it produced a reply the platform then FAILED to post — backend green,
   billing charged, room empty;
3. it produced nothing to deliver at all — same empty room.

Cases 2 and 3 are the ones a user cannot tell apart from "the agent ignored
me", and neither can they be told apart from each other by looking, which is
why they are separate message types rather than one vague "no reply" line. The
distinction is real: (2) is OUR fault and retryable, (3) is the agent's own
turn coming up empty.

The A2A shape of (3) is the expensive one. In a team room a silence is merely
confusing; in an agent↔agent DM the peer that asked is BLOCKED on the answer,
so its notice carries a mention and wakes it — a hanging errand resolves itself
instead of waiting forever (PRD 2026-08-04 §四, "A2A 空回复").

Deliberately NOT folding the NexusPower monologue into the reply as a fallback
for (3). The monologue contract promises the agent its plain text is private
deliberation; relaying it to a peer would leak reasoning the agent never
addressed to anyone (see run_collector.collect_run's ``include_monologue``).
Saying "nothing was delivered" is honest and leaks nothing.

Every function here is BEST-EFFORT and returns a verdict instead of raising:
the delivery-failure notice travels the very path that just failed, so it
failing too is the expected case. The caller falls back to the owner's inbox.
"""

from __future__ import annotations

from typing import Any, List, Optional

from loguru import logger

from xyz_agent_context.agent_framework.llm.failure import redact_secrets

# A turn that reached nobody: no reply text, and no delivery tool called.
UNDELIVERED_MSG_TYPE = "system_undelivered"

# The reply existed; posting it into the room failed.
DELIVERY_FAILED_MSG_TYPE = "system_delivery_failed"

# Cap on the error echoed into a transcript every member can read. Same budget
# as the owner-inbox notice — this text lands in a strictly wider audience, so
# it may not be more generous.
MAX_NOTICE_ERROR_LEN = 300

# English fallbacks. The frontend renders these lines from an i18n key (the
# database cannot know the reader's language); `content` is what text-only
# consumers — a log, an export, a future IM mirror — get to read.
_UNDELIVERED_TEXT = "This turn ended without delivering a reply."
_DELIVERY_FAILED_TEXT = "The reply could not be posted to this conversation."


async def announce_undelivered(
    bus: Any,
    channel_id: str,
    agent_id: str,
    *,
    mentions: Optional[List[str]] = None,
    root_run_id: Optional[str] = None,
) -> bool:
    """Say that ``agent_id``'s turn delivered nothing. True if the line landed.

    ``mentions`` is the whole difference between the two call sites. A team
    room passes none: nobody is blocked on the silence, and waking every
    member over it would be worse than the silence itself. An A2A DM passes
    the peer that asked, because that peer IS blocked and only a message wakes
    it.
    """
    return await _post(
        bus, channel_id, agent_id,
        content=_UNDELIVERED_TEXT,
        msg_type=UNDELIVERED_MSG_TYPE,
        mentions=mentions,
        root_run_id=root_run_id,
    )


async def announce_delivery_failure(
    bus: Any,
    channel_id: str,
    agent_id: str,
    *,
    error: str,
    root_run_id: Optional[str] = None,
) -> bool:
    """Say that the reply existed but could not be posted. True if it landed.

    Never mentions anyone: the reply is lost either way, and waking the room
    over our own write failure adds turns without adding information.

    The reason is carried because "it failed" without "why" is the kind of
    transparency that still leaves the user guessing — redacted first, since
    provider SDKs echo the offending credential back inside the error body and
    this string becomes a permanent transcript row.
    """
    return await _post(
        bus, channel_id, agent_id,
        content=f"{_DELIVERY_FAILED_TEXT} ({redact_secrets(error, MAX_NOTICE_ERROR_LEN)})",
        msg_type=DELIVERY_FAILED_MSG_TYPE,
        mentions=None,
        root_run_id=root_run_id,
    )


async def _post(
    bus: Any,
    channel_id: str,
    agent_id: str,
    *,
    content: str,
    msg_type: str,
    mentions: Optional[List[str]],
    root_run_id: Optional[str],
) -> bool:
    try:
        await bus.send_message(
            from_agent=agent_id,
            to_channel=channel_id,
            content=content,
            msg_type=msg_type,
            mentions=mentions,
            # Keeps this notice inside the tree that produced it, so a cascade
            # stop reaches whatever the notice goes on to wake. Every other bus
            # send carries it for the same reason.
            root_run_id=root_run_id,
        )
        return True
    except Exception as e:  # noqa: BLE001 — a notice may never become the new failure
        logger.warning(
            f"[delivery-notice] could not post {msg_type} for {agent_id} "
            f"in {channel_id}: {e}"
        )
        return False


__all__ = [
    "DELIVERY_FAILED_MSG_TYPE",
    "UNDELIVERED_MSG_TYPE",
    "announce_delivery_failure",
    "announce_undelivered",
]
