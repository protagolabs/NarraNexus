"""
@file_name: generic.py
@author: Bin Liang
@date: 2026-09-04
@description: Generic channel routes (``/api/channels/{channel}/…``): schema, bind, credential, test, unbind, set-active for ANY channel in ``ingress.channels``.

The six bespoke ``/api/<channel>/…`` routers stay during the dual-write
phase; this router is what a channel plugin (and, after 4d, the builtins'
UIs) talk to. Manager-backed channels delegate bind/test/unbind to the
data-access seam (their bespoke services keep validating with the external
platform) and read from the generic table the manager mirror keeps fresh;
plugin channels are validated against their credential schema and stored
directly. Ownership is the one canonical check (``_ownership.check_owned``).
"""
from __future__ import annotations

from typing import Any, Optional

from fastapi import APIRouter, HTTPException, Request
from loguru import logger
from pydantic import BaseModel, Field

from backend.routes._ownership import check_owned
from narranexus.contracts.channel import ChannelDescriptor
from xyz_agent_context.channel.credential_store import GenericCredentialStore, UnknownChannel, descriptor_for, missing_required

router = APIRouter()


class BindBody(BaseModel):
    agent_id: str
    fields: dict[str, Any] = Field(default_factory=dict)


class AgentBody(BaseModel):
    agent_id: str


class SetActiveBody(BaseModel):
    agent_id: str
    active: bool


async def _db():
    from xyz_agent_context.utils.db.db_factory import get_db_client

    return await get_db_client()


def _descriptor(channel: str) -> ChannelDescriptor:
    try:
        return descriptor_for(channel)
    except UnknownChannel:
        raise HTTPException(status_code=404, detail=f"unknown channel: {channel}") from None


def _schema_view(d: ChannelDescriptor) -> dict[str, Any]:
    return {
        "channel": d.name,
        "display_name": d.display_name,
        "transport": d.transport,
        "has_bind": d.has_bind,
        "has_test": d.has_test and d.credential_schema.supports_test,
        "manager_backed": bool(d.credential_manager_ref),
        "fields": [
            {"name": f.name, "kind": f.kind, "label": f.label or f.name, "help": f.help, "required": f.required, "options": list(f.options), "public": f.public}
            for f in d.credential_schema.fields
        ],
        "external_id_field": d.credential_schema.external_id_field,
    }


@router.get("/{channel}/schema")
async def channel_schema(channel: str) -> dict[str, Any]:
    return {"success": True, "data": _schema_view(_descriptor(channel))}


@router.post("/{channel}/bind")
async def channel_bind(request: Request, channel: str, body: BindBody) -> dict[str, Any]:
    d = _descriptor(channel)
    auth_err = await check_owned(request, body.agent_id)
    if auth_err:
        return {"success": False, "error": auth_err}
    missing = missing_required(d, body.fields)
    if missing:
        return {"success": False, "error": f"missing required field(s): {', '.join(missing)}"}
    db = await _db()
    if d.credential_manager_ref:
        # Bespoke validation with the external platform, then the manager mirror keeps channel_credentials fresh.
        from xyz_agent_context.module.data_access.channel_store import DirectStore

        if not d.has_bind:
            return {"success": False, "error": f"{d.display_name} binds through its own flow"}
        result = await DirectStore().bind(channel, body.agent_id, body.fields)
        if result.get("success"):
            logger.info(f"[channels] {channel} bound: agent={body.agent_id}")
        return result
    record = await GenericCredentialStore(db).upsert(channel, body.agent_id, body.fields, enabled=True)
    logger.info(f"[channels] {channel} bound: agent={body.agent_id}")
    return {"success": True, "data": record.to_public_dict()}


@router.get("/{channel}/credential")
async def channel_credential(request: Request, channel: str, agent_id: str) -> dict[str, Any]:
    _descriptor(channel)
    auth_err = await check_owned(request, agent_id)
    if auth_err:
        return {"success": False, "error": auth_err}
    public = await GenericCredentialStore(await _db()).get_public(channel, agent_id)
    return {"success": True, "data": public}


@router.post("/{channel}/test")
async def channel_test(request: Request, channel: str, body: AgentBody) -> dict[str, Any]:
    d = _descriptor(channel)
    auth_err = await check_owned(request, body.agent_id)
    if auth_err:
        return {"success": False, "error": auth_err}
    if d.credential_manager_ref and d.has_test:
        from xyz_agent_context.module.data_access.channel_store import DirectStore

        return await DirectStore().test_connection(channel, body.agent_id)
    record = await GenericCredentialStore(await _db()).get(channel, body.agent_id)
    if record is None:
        return {"success": False, "error": f"no {d.display_name} credential bound for this agent"}
    return {"success": True, "data": {"checked": False, "enabled": record.enabled}}


@router.post("/{channel}/unbind")
async def channel_unbind(request: Request, channel: str, body: AgentBody) -> dict[str, Any]:
    d = _descriptor(channel)
    auth_err = await check_owned(request, body.agent_id)
    if auth_err:
        return {"success": False, "error": auth_err}
    db = await _db()
    if d.credential_manager_ref:
        from xyz_agent_context.module.data_access.channel_store import DirectStore

        result = await DirectStore().unbind(channel, body.agent_id)
        return result
    removed = await GenericCredentialStore(db).unbind(channel, body.agent_id)
    if not removed:
        return {"success": False, "error": f"no {d.display_name} credential bound for this agent"}
    logger.info(f"[channels] {channel} unbound: agent={body.agent_id}")
    return {"success": True, "data": {"unbound": True}}


@router.post("/{channel}/set-active")
async def channel_set_active(request: Request, channel: str, body: SetActiveBody) -> dict[str, Any]:
    d = _descriptor(channel)
    auth_err = await check_owned(request, body.agent_id)
    if auth_err:
        return {"success": False, "error": auth_err}
    db = await _db()
    if d.credential_manager_ref:
        manager = d.resolve(d.credential_manager_ref)(db)
        setter: Optional[Any] = getattr(manager, "set_enabled", None)
        if setter is None:
            return {"success": False, "error": f"{d.display_name} does not support activation toggling"}
        ok = await setter(body.agent_id, body.active)
    else:
        ok = await GenericCredentialStore(db).set_enabled(channel, body.agent_id, body.active)
    if not ok:
        return {"success": False, "error": f"No {d.display_name} credential bound to this agent."}
    return {"success": True, "enabled": body.active}


__all__ = ["router"]
