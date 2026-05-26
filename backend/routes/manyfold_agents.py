"""
@file_name: manyfold_agents.py
@author: NexusAgent
@date: 2026-05-25
@description: Cross-user agent listing + create endpoint for Manyfold platform

Manyfold needs to:
  - Enumerate all agents in the container regardless of which NarraNexus
    user created them (GET /manyfold/agents).
  - Create a new NarraNexus user + agent when a Manyfold user creates an
    agent via the Manyfold UI (POST /manyfold/agents).

Registered only when ENABLE_MANYFOLD_API=1 (see backend/main.py). The
auth middleware requires a valid MANYFOLD_GATEWAY_TOKEN before the
handler runs.

Owner decision 2026-05-25: container is single-user in practice so the
cross-user concern is mostly cosmetic, but the platform contract still
expects "list everything" semantics — we honor it.
"""

from __future__ import annotations

import re
from typing import Optional

from fastapi import APIRouter, HTTPException, Request
from loguru import logger
from pydantic import BaseModel, Field

from xyz_agent_context.utils.db_factory import get_db_client


router = APIRouter()


def _require_manyfold_auth(request: Request) -> None:
    if not getattr(request.state, "manyfold_authed", False):
        raise HTTPException(
            status_code=401,
            detail="missing or invalid MANYFOLD_GATEWAY_TOKEN",
        )


@router.get("/manyfold/agents")
async def list_all_agents(request: Request):
    """Return every agent row in the container, cross-user.

    Shape mirrors what Manyfold's frameworkOptions expects (id + name +
    description), plus created_by / created_at for traceability.
    """
    _require_manyfold_auth(request)

    db = await get_db_client()
    rows = await db.get("agents", {}) or []
    return {
        "data": [
            {
                "agent_id": row.get("agent_id"),
                "name": row.get("agent_name"),
                "description": row.get("agent_description"),
                "agent_type": row.get("agent_type"),
                "created_by": row.get("created_by"),
                "created_at": row.get("agent_create_time"),
                "is_public": bool(row.get("is_public", 0)),
            }
            for row in rows
        ],
        "object": "list",
    }


# ---------------------------------------------------------------------------
# POST /manyfold/agents — Manyfold side creates an agent, we auto-create
# the matching NarraNexus user (if missing) and the agent row.
# ---------------------------------------------------------------------------


_USER_ID_RE = re.compile(r"[^a-zA-Z0-9_\-]+")


def _normalize_user_id(manyfold_user_id: str) -> str:
    """Turn a Manyfold-side user_id into a NarraNexus-safe one.

    NarraNexus user_id is a TEXT primary key; we prefix with ``mf_`` to
    make it visually obvious where the row came from and to avoid
    collisions with native NarraNexus user_ids (``bin``, ``local-default``
    etc).
    """
    cleaned = _USER_ID_RE.sub("_", manyfold_user_id.strip())[:60]
    if not cleaned:
        raise ValueError("manyfold_user_id normalises to an empty string")
    return cleaned if cleaned.startswith("mf_") else f"mf_{cleaned}"


class ManyfoldCreateAgentRequest(BaseModel):
    """Body Manyfold's NarraNexus adapter sends when provisioning an agent.

    All fields except ``agent_id`` and ``manyfold_user_id`` are optional;
    we pick sensible defaults for the rest.
    """

    agent_id: str
    agent_name: str = ""
    description: Optional[str] = None
    manyfold_user_id: str
    manyfold_user_email: Optional[str] = None
    display_name: Optional[str] = None
    inherit_provider_from: Optional[str] = Field(
        default=None,
        description=(
            "Optional native NarraNexus user_id (e.g. 'bin') to copy "
            "provider rows and slot bindings from. Lets a Manyfold-side "
            "agent inherit a host operator's pre-configured LLM setup "
            "without forcing the end user to re-run /api/providers "
            "themselves on first chat."
        ),
    )


@router.post("/manyfold/agents")
async def create_agent_for_manyfold(
    request: Request,
    body: ManyfoldCreateAgentRequest,
):
    """Idempotent agent + user provisioning called by the Manyfold adapter.

    Behaviour:
      1. Ensure a NarraNexus user row exists for the Manyfold user
         (normalised id: ``mf_<sanitised>``). If the row is new, copy
         provider configuration from ``inherit_provider_from`` (when
         supplied and present) so chat works on the first turn.
      2. Ensure an agent row exists with ``created_by`` set to the user
         from step 1. If it already exists, just update the name /
         description to keep them in sync with Manyfold's side.

    Returns the canonical NarraNexus agent_id + the (possibly newly
    created) NarraNexus user_id so the caller can stash it.
    """
    _require_manyfold_auth(request)
    db = await get_db_client()

    nx_user_id = _normalize_user_id(body.manyfold_user_id)
    user_row = await db.get_one("users", {"user_id": nx_user_id})
    user_created = False
    if not user_row:
        await db.insert("users", {
            "user_id": nx_user_id,
            "user_type": "local",
            "role": "user",
            "display_name": body.display_name
                or body.manyfold_user_email
                or body.manyfold_user_id,
        })
        user_created = True
        logger.info(
            f"[manyfold-create] new NarraNexus user {nx_user_id!r} "
            f"(manyfold_id={body.manyfold_user_id!r})"
        )

    # If asked, mirror provider rows + slot bindings from a template user.
    # Skip on idempotent reruns where the user already had slots configured.
    if user_created and body.inherit_provider_from:
        try:
            await _clone_provider_setup(
                db, src_user_id=body.inherit_provider_from, dst_user_id=nx_user_id
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                f"[manyfold-create] provider clone {body.inherit_provider_from} "
                f"→ {nx_user_id} failed: {exc}"
            )

    # Agent row — insert or update
    agent_row = await db.get_one("agents", {"agent_id": body.agent_id})
    if agent_row:
        await db.update("agents", {"agent_id": body.agent_id}, {
            "agent_name": body.agent_name or agent_row.get("agent_name") or body.agent_id,
            "agent_description": body.description or agent_row.get("agent_description"),
            "created_by": nx_user_id,
        })
        agent_created = False
    else:
        await db.insert("agents", {
            "agent_id": body.agent_id,
            "agent_name": body.agent_name or body.agent_id,
            "agent_description": body.description or "",
            "agent_type": "general",
            "created_by": nx_user_id,
            "is_public": 0,
        })
        agent_created = True

    logger.info(
        f"[manyfold-create] agent {body.agent_id!r} "
        f"{'created' if agent_created else 'updated'} "
        f"created_by={nx_user_id!r}"
    )

    return {
        "agent_id": body.agent_id,
        "user_id": nx_user_id,
        "user_created": user_created,
        "agent_created": agent_created,
    }


async def _clone_provider_setup(db, *, src_user_id: str, dst_user_id: str) -> None:
    """Copy user_providers + user_slots rows from src → dst.

    Critical: NarraNexus enforces per-user provider visibility (the
    provider_resolver refuses to serve a slot whose ``provider_id`` is
    owned by a different user). So we generate fresh ``provider_id``\\s
    for the destination user, build an old→new map, and rewrite the
    cloned slot bindings to point at those new ids. Without this remap,
    the destination's slot references the source's provider_id and the
    very first chat turn fails with ``provider X not visible``.
    """
    import secrets

    src_providers = await db.get("user_providers", {"user_id": src_user_id}) or []
    if not src_providers:
        logger.info(
            f"[manyfold-create] no providers to clone from {src_user_id!r}"
        )
        return

    existing_dst_provider_names = {
        row.get("name")
        for row in (await db.get("user_providers", {"user_id": dst_user_id}) or [])
    }

    pid_remap: dict[str, str] = {}
    for prov in src_providers:
        old_pid = prov.get("provider_id")
        if prov.get("name") in existing_dst_provider_names:
            continue
        new_pid = f"prov_{secrets.token_hex(4)}"
        pid_remap[old_pid] = new_pid
        clone = {k: v for k, v in prov.items() if k != "id"}
        clone["user_id"] = dst_user_id
        clone["provider_id"] = new_pid
        if clone.get("owner_user_id") == src_user_id:
            clone["owner_user_id"] = dst_user_id
        await db.insert("user_providers", clone)

    src_slots = await db.get("user_slots", {"user_id": src_user_id}) or []
    existing_dst_slot_names = {
        row.get("slot_name")
        for row in (await db.get("user_slots", {"user_id": dst_user_id}) or [])
    }
    for slot in src_slots:
        if slot.get("slot_name") in existing_dst_slot_names:
            continue
        clone = {k: v for k, v in slot.items() if k != "id"}
        clone["user_id"] = dst_user_id
        old_pid = clone.get("provider_id")
        clone["provider_id"] = pid_remap.get(old_pid, old_pid)
        await db.insert("user_slots", clone)

    logger.info(
        f"[manyfold-create] cloned {len(src_providers)} providers + "
        f"{len(src_slots)} slot bindings: {src_user_id} → {dst_user_id} "
        f"(pid remap: {pid_remap})"
    )
