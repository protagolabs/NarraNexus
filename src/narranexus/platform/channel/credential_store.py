"""
@file_name: credential_store.py
@author: Bin Liang
@date: 2026-09-04
@description: ``GenericCredentialStore`` — the one credential store every IM channel is served from (table ``channel_credentials``).

Values are split by the channel's ``CredentialSchema``: declared secret
fields (and anything that looks like one — token / secret / password / key —
unless the schema declares it public) go encrypted into ``secret_json``,
identity fields into ``public_json``, and the schema's ``external_id_field``
into the channel-wide unique ``external_id``. Every channel — the six
builtin managers and plugin channels through the generic routes — reads and
writes here (batch 4d); the retired per-channel tables are copied in once by
``credential_legacy``. The descriptor comes from the ``ingress.channels`` registry.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any, Optional

from narranexus.contracts.channel import ChannelDescriptor, CredentialField
from narranexus.platform.channel.credential_codec import decode_secrets, encode_secrets
from narranexus.platform.utils import utc_now

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
    version: int = 0

    @property
    def app_id(self) -> str:
        """The subscriber key ChannelTriggerBase expects on a credential (external id, else the agent id)."""
        return self.external_id or self.agent_id

    def to_public_dict(self) -> dict[str, Any]:
        return {"channel": self.channel, "agent_id": self.agent_id, "enabled": self.enabled, "external_id": self.external_id, **self.public}

    def to_raw_dict(self) -> dict[str, Any]:
        return {**self.to_public_dict(), **self.secret}


def descriptor_for(channel: str, registries: Any = None) -> ChannelDescriptor:
    import narranexus.platform.module_system  # noqa: F401 — registers the builtin descriptors (idempotent)

    regs = registries
    if regs is None:
        from narranexus.kernel.plugins.registries import KERNEL_REGISTRIES

        regs = KERNEL_REGISTRIES
    registry = regs.registry_for("ingress.channels")
    if channel not in registry:
        raise UnknownChannel(f"unknown channel: {channel!r}")
    return registry.get(channel)


def all_descriptors(registries: Any = None) -> tuple[ChannelDescriptor, ...]:
    """Every registered channel descriptor (builtin and plugin), registration order."""
    import narranexus.platform.module_system  # noqa: F401 — registers the builtin descriptors (idempotent)

    regs = registries
    if regs is None:
        from narranexus.kernel.plugins.registries import KERNEL_REGISTRIES

        regs = KERNEL_REGISTRIES
    return tuple(entry.factory() for entry in regs.registry_for("ingress.channels").entries())


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


def bind_fields_for(descriptor: ChannelDescriptor) -> tuple[CredentialField, ...]:
    """The fields a bind call accepts: the descriptor's ``bind_fields`` (manager-backed
    channels whose service takes something other than the stored shape) or the stored schema."""
    return descriptor.bind_fields or descriptor.credential_schema.fields


def validate_bind_fields(descriptor: ChannelDescriptor, values: dict[str, Any]) -> Optional[str]:
    """Fail-closed check of a bind body against ``bind_fields_for``: unknown names are
    rejected (they would reach a service's ``do_bind(**fields)``), required ones must be
    non-blank, a ``select`` must be one of its options. Returns the error text or None."""
    fields = bind_fields_for(descriptor)
    known = {f.name: f for f in fields}
    unknown = sorted(k for k in values if k not in known)
    if unknown:
        return f"unknown field(s): {', '.join(unknown)}"
    missing = [f.name for f in fields if f.required and not str(values.get(f.name, "") or "").strip()]
    if missing:
        return f"missing required field(s): {', '.join(missing)}"
    for f in fields:
        v = values.get(f.name)
        if f.kind == "select" and f.options and v not in (None, "") and str(v) not in f.options:
            return f"{f.name} must be one of: {', '.join(f.options)}"
    return None


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
            version=int(row.get("version") or 0),
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
            row["version"] = int(existing.get("version") or 0) + 1
            await self._db.update(TABLE, {"channel": channel, "agent_id": agent_id}, row)
        else:
            row.setdefault("enabled", 1)
            await self._db.insert(TABLE, {"channel": channel, "agent_id": agent_id, "created_at": utc_now(), "version": 0, **row})
        record = await self.get(channel, agent_id)
        assert record is not None
        return record

    async def patch(self, channel: str, agent_id: str, fields: dict[str, Any], *, expect: Optional[dict[str, Any]] = None, enabled: Optional[bool] = None) -> Optional[CredentialRecord]:
        """Merge ``fields`` into the stored values with optimistic concurrency.

        Re-reads and retries when the row's ``version`` moved under us, so two
        writers patching DISJOINT fields (the bind panel and a trigger's
        auth-status update) never clobber each other. ``expect`` is a
        compare-and-set on current values (``{"owner_user_id": ""}`` = only
        while unresolved). Returns the record, or None when the binding is
        missing or ``expect`` does not hold.
        """
        descriptor = self.descriptor(channel)
        for _ in range(8):
            current = await self.get(channel, agent_id)
            if current is None:
                return None
            merged_values = {**current.public, **current.secret}
            if expect is not None and any(merged_values.get(k, "") != v for k, v in expect.items()):
                return None
            merged_values.update(fields)
            public, secret, external_id = split_values(descriptor, merged_values)
            row = {
                "external_id": external_id,
                "public_json": json.dumps(public, sort_keys=True, default=str),
                "secret_json": encode_secrets(secret),
                "updated_at": utc_now(),
                "version": current.version + 1,
            }
            if enabled is not None:
                row["enabled"] = 1 if enabled else 0
            affected = await self._db.update(TABLE, {"channel": channel, "agent_id": agent_id, "version": current.version}, row)
            if affected:
                return await self.get(channel, agent_id)
        raise RuntimeError(f"{channel}/{agent_id}: credential patch kept losing the version race")

    async def update(self, channel: str, agent_id: str, patch: dict[str, Any]) -> Optional[CredentialRecord]:
        """Merge ``patch`` into the stored values (missing binding → None)."""
        return await self.patch(channel, agent_id, patch)

    async def update_if(self, channel: str, agent_id: str, expect: dict[str, Any], fields: dict[str, Any]) -> bool:
        """Compare-and-set: apply ``fields`` only while ``expect`` holds; False when it does not (or no binding)."""
        return await self.patch(channel, agent_id, fields, expect=expect) is not None

    async def find_one(self, channel: str, *, external_id: Optional[str] = None, **public_equals: Any) -> Optional[CredentialRecord]:
        """The binding whose external id / public fields match (channel-wide lookups: 'is this bot already bound?')."""
        if external_id is not None:
            row = await self._db.get_one(TABLE, {"channel": channel, "external_id": external_id})
            if row is None:
                return None
            record = self._row_to_record(row)
            if all(record.public.get(k) == v for k, v in public_equals.items()):
                return record
            return None
        for record in await self.list_all(channel):
            if all(record.public.get(k) == v for k, v in public_equals.items()):
                return record
        return None

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

    async def list_for_agent(self, agent_id: str) -> list[CredentialRecord]:
        """Every channel binding of one agent (bundle export, agent deletion)."""
        rows = await self._db.get(TABLE, {"agent_id": agent_id})
        return [self._row_to_record(r) for r in rows]

    async def list_all(self, channel: str) -> list[CredentialRecord]:
        rows = await self._db.get(TABLE, {"channel": channel})
        return [self._row_to_record(r) for r in rows]


__all__ = ["CredentialRecord", "GenericCredentialStore", "TABLE", "UnknownChannel", "all_descriptors", "bind_fields_for", "descriptor_for", "missing_required", "split_values", "validate_bind_fields"]
