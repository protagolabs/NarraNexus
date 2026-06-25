"""
@file_name: external_identity.py
@author: NetMind.AI
@date: 2026-06-24
@description: External IM subject identity — the per-conversation scope id used to
run external IM turns as their own isolated tenants.

An external IM turn runs the full AgentRuntime with `scope_to_owner=False` and this
subject id as `user_id`, so narrative / workspace / memory / executor container all
isolate per subject (see agent_runtime._resolve_scope_user_id). Billing stays with
the agent owner (resolved off agent_id).

Identity is ROOM-DERIVED:
- a DM room (1:1) → a per-person scope (the DM room is unique to that person);
- a group room → a per-group (community) scope, shared by its members.
The room_id is the discriminator, so a single rule covers both. The "ext_" prefix
lets the executor/broker recognise external subjects (and skip real-user checks).

The id is DOCKER-SAFE: only `[a-z0-9_]` (prefix + channel + hex hash, all separated
by "_") — no colons or path-hostile characters. The cloud broker uses it directly
as a container-name component and a workspace volume-subpath, so it must avoid `:`
(forbidden in docker names) and any char that's fragile in mount specs / paths.
"""
from __future__ import annotations

import hashlib

_EXTERNAL_PREFIX = "ext"

# room_id is hashed to this many hex chars (64 bits). The scope id must fit
# users.user_id VARCHAR(64) (the universal user_id width) AND be a filesystem-safe
# workspace dir component — but raw IM room ids are unbounded (Matrix
# "!opaque:homeserver", etc.) and may contain path-hostile characters. 64 bits is
# collision-safe for any realistic per-owner external population (birthday bound
# ~2^32). The original (channel, room_id) is recorded in the users-row metadata at
# provisioning time for reverse lookup.
_ROOM_HASH_LEN = 16


def external_subject_id(channel: str, room_id: str) -> str:
    """Build the bounded scope identity for an external IM conversation.

    Args:
        channel: IM channel name (e.g. "slack", "narramessenger").
        room_id: IM-side room/chat/group id (the DM-vs-group discriminator).

    Returns:
        A stable id of the form ``ext_{channel}_{room_hash}`` (<= 64 chars,
        docker-safe: ``[a-z0-9_]`` only).

    Raises:
        ValueError: if channel or room_id is empty — an empty component would
            collapse distinct conversations onto one scope (a data-isolation bug).
    """
    if not channel or not room_id:
        raise ValueError(
            f"external_subject_id requires non-empty channel and room_id "
            f"(got channel={channel!r}, room_id={room_id!r})"
        )
    room_hash = hashlib.sha256(room_id.encode("utf-8")).hexdigest()[:_ROOM_HASH_LEN]
    return f"{_EXTERNAL_PREFIX}_{channel}_{room_hash}"


def is_external_subject(user_id: str) -> bool:
    """True if user_id is an external IM subject (vs a real platform user)."""
    return bool(user_id) and user_id.startswith(f"{_EXTERNAL_PREFIX}_")


# user_type marker for auto-provisioned external IM identities.
EXTERNAL_USER_TYPE = "external_im"


async def ensure_external_user(
    db,
    *,
    subject_id: str,
    channel: str,
    room_id: str,
    display_name: str | None = None,
    owner_user_id: str | None = None,
) -> None:
    """Idempotently provision a persistent ``users`` row for an external subject.

    External IM identities are first-class persistent users (not ephemeral), so they
    survive across turns and are listable/manageable. Called on every external IM
    turn; the get-or-create is cheap and a concurrent first-message race is absorbed
    by the user_id UNIQUE constraint.

    Best-effort: a provisioning failure must NOT block the agent run — the scope
    isolation works off the user_id string regardless of whether the row exists, so
    on error we log and continue. The original (channel, room_id, owner) is recorded
    in ``metadata`` for reverse lookup.
    """
    if db is None or not subject_id:
        return
    try:
        existing = await db.get_one("users", {"user_id": subject_id})
        if existing:
            return
        import json
        await db.insert(
            "users",
            {
                "user_id": subject_id,
                "user_type": EXTERNAL_USER_TYPE,
                "display_name": (display_name or subject_id)[:255],
                "metadata": json.dumps(
                    {
                        "channel": channel,
                        "room_id": room_id,
                        "owner_user_id": owner_user_id,
                    }
                ),
            },
        )
    except Exception as e:  # noqa: BLE001
        msg = str(e)
        if "UNIQUE constraint failed" in msg or "Duplicate entry" in msg or "1062" in msg:
            # Concurrent first-message raced us; the row exists now. Fine.
            return
        from loguru import logger
        logger.warning(
            f"ensure_external_user({subject_id}) failed (non-fatal, scope still "
            f"isolates by user_id): {type(e).__name__}: {e}"
        )
