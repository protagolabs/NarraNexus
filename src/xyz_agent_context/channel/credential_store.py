"""
@file_name: credential_store.py
@author: Bin Liang
@date: 2026-09-04
@description: ``GenericCredentialStore`` — the one credential store every IM channel is served from (table ``channel_credentials``).

Values are split by the channel's ``CredentialSchema``: declared secret
fields (and anything that looks like one — token / secret / password / key —
unless the schema declares it public) go encrypted into ``secret_json``,
identity fields into ``public_json``, and the schema's ``external_id_field``
into the channel-wide unique ``external_id``. Builtin channels are mirrored
into this table from their bespoke managers during the dual-write phase
(``credential_mirror``); plugin channels write here directly through the
generic routes. The descriptor comes from the ``ingress.channels`` registry.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any, Optional

from narranexus.contracts.channel import ChannelDescriptor
from xyz_agent_context.channel.credential_codec import decode_secrets, encode_secrets
from xyz_agent_context.utils import utc_now

TABLE = "channel_credentials"
_SECRET_LIKE = re.compile(r"(token|secret|password|passwd|api_key|apikey|private_key)", re.IGNORECASE)


class UnknownChannel(ValueError):
    pass


@dataclass(frozen=True)
class CredentialRecord:
    channel: str
    agent_id: str
    enabled: bool
    external_id: Optional[str]
    public: dict[str, Any] = field(default_factory=dict)
    secret: dict[str, Any] = field(default_factory=dict)
    created_at: Any = None
    updated_at: Any = None

    def to_public_dict(self) -> dict[str, Any]:
        return {"channel": self.channel, "agent_id": self.agent_id, "enabled": self.enabled, "external_id": self.external_id, **self.public}

    def to_raw_dict(self) -> dict[str, Any]:
        return {**self.to_public_dict(), **self.secret}


def descriptor_for(channel: str, registries: Any = None) -> ChannelDescriptor:
    import xyz_agent_context.module  # noqa: F401 — registers the builtin descriptors (idempotent)

    regs = registries
    if regs is None:
        from narranexus.kernel.plugins.registries import KERNEL_REGISTRIES

        regs = KERNEL_REGISTRIES
    registry = regs.registry_for("ingress.channels")
    if channel not in registry:
        raise UnknownChannel(f"unknown channel: {channel!r}")
    return registry.get(channel)


def split_values(descriptor: ChannelDescriptor, values: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any], Optional[str]]:
    """(public, secret, external_id) for ``values`` under the descriptor's schema."""
    schema = descriptor.credential_schema
    declared_secret = set(schema.secret_names())
    declared_public = set(schema.public_names())
    public: dict[str, Any] = {}
    secret: dict[str, Any] = {}
    for key, value in values.items():
        if key in ("channel", "agent_id", "enabled", "external_id", "id", "created_at", "updated_at"):
            continue
        if key in declared_secret or (key not in declared_public and _SECRET_LIKE.search(key)):
            secret[key] = value
        else:
            public[key] = value
    external = values.get(schema.external_id_field) if schema.external_id_field else None
    return public, secret, (str(external) if external not in (None, "") else None)


def missing_required(descriptor: ChannelDescriptor, values: dict[str, Any]) -> tuple[str, ...]:
    return tuple(f.name for f in descriptor.credential_schema.fields if f.required and not str(values.get(f.name, "") or "").strip())


class GenericCredentialStore:
    def __init__(self, db: Any, registries: Any = None) -> None:
        self._db = db
        self._registries = registries

    def descriptor(self, channel: str) -> ChannelDescriptor:
        return descriptor_for(channel, self._registries)

    @staticmethod
    def _row_to_record(row: dict[str, Any]) -> CredentialRecord:
        public = json.loads(row.get("public_json") or "{}")
        return CredentialRecord(
            channel=row["channel"],
            agent_id=row["agent_id"],
            enabled=bool(row.get("enabled", 1)),
            external_id=row.get("external_id") or None,
            public=public if isinstance(public, dict) else {},
            secret=decode_secrets(row.get("secret_json") or ""),
            created_at=row.get("created_at"),
            updated_at=row.get("updated_at"),
        )

    async def upsert(self, channel: str, agent_id: str, values: dict[str, Any], *, enabled: Optional[bool] = None) -> CredentialRecord:
        """Create or replace the (channel, agent) binding from a flat value dict (public + secret fields)."""
        descriptor = self.descriptor(channel)
        public, secret, external_id = split_values(descriptor, values)
        existing = await self._db.get_one(TABLE, {"channel": channel, "agent_id": agent_id})
        row = {
            "external_id": external_id,
            "public_json": json.dumps(public, sort_keys=True, default=str),
            "secret_json": encode_secrets(secret),
            "updated_at": utc_now(),
        }
        if enabled is not None:
            row["enabled"] = 1 if enabled else 0
        if existing:
            await self._db.update(TABLE, {"channel": channel, "agent_id": agent_id}, row)
        else:
            row.setdefault("enabled", 1)
            await self._db.insert(TABLE, {"channel": channel, "agent_id": agent_id, **row})
        record = await self.get(channel, agent_id)
        assert record is not None
        return record

    async def update(self, channel: str, agent_id: str, patch: dict[str, Any]) -> Optional[CredentialRecord]:
        """Merge ``patch`` into the stored values (missing binding → None)."""
        current = await self.get(channel, agent_id)
        if current is None:
            return None
        merged = {**current.public, **current.secret, **patch}
        return await self.upsert(channel, agent_id, merged)

    async def get(self, channel: str, agent_id: str) -> Optional[CredentialRecord]:
        row = await self._db.get_one(TABLE, {"channel": channel, "agent_id": agent_id})
        return self._row_to_record(row) if row else None

    async def get_public(self, channel: str, agent_id: str) -> Optional[dict[str, Any]]:
        record = await self.get(channel, agent_id)
        return record.to_public_dict() if record else None

    async def unbind(self, channel: str, agent_id: str) -> bool:
        existing = await self._db.get_one(TABLE, {"channel": channel, "agent_id": agent_id})
        if not existing:
            return False
        await self._db.delete(TABLE, {"channel": channel, "agent_id": agent_id})
        return True

    async def set_enabled(self, channel: str, agent_id: str, enabled: bool) -> bool:
        existing = await self._db.get_one(TABLE, {"channel": channel, "agent_id": agent_id})
        if not existing:
            return False
        await self._db.update(TABLE, {"channel": channel, "agent_id": agent_id}, {"enabled": 1 if enabled else 0, "updated_at": utc_now()})
        return True

    async def list_active(self, channel: str) -> list[CredentialRecord]:
        rows = await self._db.get(TABLE, {"channel": channel, "enabled": 1})
        return [self._row_to_record(r) for r in rows]

    async def list_all(self, channel: str) -> list[CredentialRecord]:
        rows = await self._db.get(TABLE, {"channel": channel})
        return [self._row_to_record(r) for r in rows]


__all__ = ["CredentialRecord", "GenericCredentialStore", "TABLE", "UnknownChannel", "descriptor_for", "missing_required", "split_values"]
