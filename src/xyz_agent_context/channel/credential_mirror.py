"""
@file_name: credential_mirror.py
@author: Bin Liang
@date: 2026-09-04
@description: Dual-write phase of the credential migration (batch 4b): every write through a builtin channel's bespoke credential manager is mirrored into ``channel_credentials``.

Rather than editing six managers with six different write vocabularies,
``install_manager_mirrors`` wraps each manager class's write methods (the
known set below) so that after the original completes the row is re-read
through the manager's read method and upserted (or deleted) in the generic
store — one mapping (``to_raw_dict`` + the descriptor's schema) instead of
six. ``backfill`` copies the active rows once at boot (migration m0004).
Reads still go to the bespoke tables until batch 4d switches them; then
this module is deleted.
"""
from __future__ import annotations

import inspect
from typing import Any, Optional

from loguru import logger

from xyz_agent_context.channel.credential_store import GenericCredentialStore

WRITE_METHODS = (
    "bind", "unbind", "set_enabled", "update_bot_identity", "update_owner", "upsert",
    "update_since_token", "update_device_id", "save_credential", "save_raw",
    "update_auth_status", "delete_credential",
)
_MARK = "_nx_credential_mirror"


def _manager_db(manager: Any) -> Any:
    return getattr(manager, "_db", None) or getattr(manager, "db", None)


def _agent_id_of(args: tuple[Any, ...], kwargs: dict[str, Any]) -> Optional[str]:
    if "agent_id" in kwargs:
        return str(kwargs["agent_id"])
    if args:
        first = args[0]
        if isinstance(first, str):
            return first
        agent_id = getattr(first, "agent_id", None)
        if isinstance(agent_id, str):
            return agent_id
    return None


async def sync_from_manager(db: Any, channel: str, agent_id: str, *, registries: Any = None) -> Optional[dict[str, Any]]:
    """Re-read (channel, agent) through the bespoke manager and mirror it; returns the public view or None (deleted)."""
    store = GenericCredentialStore(db, registries)
    descriptor = store.descriptor(channel)
    if not descriptor.credential_manager_ref:
        return None
    manager = descriptor.resolve(descriptor.credential_manager_ref)(db)
    cred = await getattr(manager, descriptor.credential_read_method)(agent_id)
    if cred is None:
        await store.unbind(channel, agent_id)
        return None
    raw = cred.to_raw_dict() if hasattr(cred, "to_raw_dict") else dict(cred)
    enabled = raw.get("enabled", raw.get("is_active", True))
    record = await store.upsert(channel, agent_id, raw, enabled=bool(enabled))
    return record.to_public_dict()


def _wrap(cls: type, name: str, channel: str, registries: Any) -> None:
    original = getattr(cls, name, None)
    if original is None or not inspect.iscoroutinefunction(original) or getattr(original, _MARK, False):
        return

    async def mirrored(self, *args: Any, **kwargs: Any):
        result = await original(self, *args, **kwargs)
        agent_id = _agent_id_of(args, kwargs)
        db = _manager_db(self)
        if agent_id and db is not None:
            try:
                await sync_from_manager(db, channel, agent_id, registries=registries)
            except Exception as exc:  # noqa: BLE001 — the mirror must never break the bespoke write
                logger.warning(f"[credential-mirror] {channel}/{agent_id}: mirror after {name} failed: {exc}")
        return result

    setattr(mirrored, _MARK, True)
    mirrored.__name__ = original.__name__
    mirrored.__qualname__ = original.__qualname__
    mirrored.__doc__ = original.__doc__
    setattr(cls, name, mirrored)


def install_manager_mirrors(registries: Any = None) -> tuple[str, ...]:
    """Wrap every manager-backed channel's write methods (idempotent). Returns the channels wired."""
    from xyz_agent_context.channel.credential_store import descriptor_for

    regs = registries
    if regs is None:
        from narranexus.kernel.plugins.registries import KERNEL_REGISTRIES

        regs = KERNEL_REGISTRIES
    wired: list[str] = []
    for entry in regs.registry_for("ingress.channels").entries():
        descriptor = descriptor_for(entry.name, regs)
        if not descriptor.credential_manager_ref:
            continue
        try:
            cls = descriptor.resolve(descriptor.credential_manager_ref)
        except Exception as exc:  # noqa: BLE001 — a channel whose SDK is absent has no manager to mirror
            logger.debug(f"[credential-mirror] {entry.name}: manager unavailable, not mirrored: {exc}")
            continue
        for name in WRITE_METHODS:
            _wrap(cls, name, descriptor.name, regs)
        wired.append(descriptor.name)
    return tuple(wired)


async def backfill(db: Any, *, registries: Any = None) -> dict[str, int]:
    """Copy every active bespoke binding into the generic table (idempotent). Returns rows per channel."""
    from xyz_agent_context.channel.credential_store import descriptor_for

    regs = registries
    if regs is None:
        from narranexus.kernel.plugins.registries import KERNEL_REGISTRIES

        regs = KERNEL_REGISTRIES
    counts: dict[str, int] = {}
    for entry in regs.registry_for("ingress.channels").entries():
        descriptor = descriptor_for(entry.name, regs)
        if not descriptor.credential_manager_ref:
            continue
        try:
            manager = descriptor.resolve(descriptor.credential_manager_ref)(db)
        except Exception as exc:  # noqa: BLE001
            logger.debug(f"[credential-mirror] {entry.name}: manager unavailable, not backfilled: {exc}")
            continue
        lister = getattr(manager, "list_active", None) or getattr(manager, "get_active_credentials", None)
        if lister is None:
            continue
        n = 0
        for cred in await lister():
            agent_id = getattr(cred, "agent_id", None)
            if not agent_id:
                continue
            try:
                await sync_from_manager(db, descriptor.name, agent_id, registries=regs)
                n += 1
            except Exception as exc:  # noqa: BLE001
                logger.warning(f"[credential-mirror] {descriptor.name}/{agent_id}: backfill failed: {exc}")
        counts[descriptor.name] = n
    return counts


__all__ = ["WRITE_METHODS", "backfill", "install_manager_mirrors", "sync_from_manager"]
