"""
@file_name: channel.py
@author: Bin Liang
@date: 2026-09-04
@description: Contract for IM channels (slot ``ingress.channels``) — one descriptor per channel.

A channel used to be six scattered facts: a trigger class in the trigger
map, a module in MODULE_MAP, a credential manager + bind service in the
data-access ``CHANNELS`` table, a ``WorkingSource`` enum member, a
message-source handler and a hard-coded frontend row. ``ChannelDescriptor``
is that record as data: the platform reads every one of those tables from
the registry, and a channel plugin is one descriptor plus its classes.

``credential_schema`` is the generic bind form (batch 4b) — fields the
platform stores split into public and secret halves; ``transport`` says how
events arrive (``socket`` = the trigger keeps a connection, ``poll`` = it
polls, ``webhook`` = the platform's webhook endpoint feeds it, ``none`` =
credentials only, no inbound). Class references are ``"pkg.mod:Name"``
strings resolved lazily so declaring a channel never imports its SDK.

Contract version: ``API_VERSIONS["channel"]``.
"""
from __future__ import annotations

import importlib
from dataclasses import dataclass, field
from typing import Any, Literal, Mapping, Optional

Transport = Literal["socket", "poll", "webhook", "none"]
FieldKind = Literal["string", "secret", "url", "bool", "int", "select"]


@dataclass(frozen=True)
class CredentialField:
    name: str
    kind: FieldKind = "string"
    label: str = ""
    help: str = ""
    required: bool = True
    options: tuple[str, ...] = ()
    # Shown back to the UI after bind (identity fields such as bot_username).
    public: bool = True

    def __post_init__(self) -> None:
        if not self.name.isidentifier():
            raise ValueError(f"credential field name must be an identifier, got {self.name!r}")
        if self.kind == "secret" and self.public:
            object.__setattr__(self, "public", False)


@dataclass(frozen=True)
class CredentialSchema:
    fields: tuple[CredentialField, ...] = ()
    # The platform can probe the connection after bind (descriptor.service_ref: do_test_connection).
    supports_test: bool = True
    # Identity field the platform treats as the channel-wide unique external id (bot id, app id).
    external_id_field: str = ""

    def secret_names(self) -> tuple[str, ...]:
        return tuple(f.name for f in self.fields if f.kind == "secret" or not f.public)

    def public_names(self) -> tuple[str, ...]:
        return tuple(f.name for f in self.fields if f.kind != "secret" and f.public)


@dataclass(frozen=True)
class ChannelUi:
    label: str
    icon: str = "message-square"  # lucide icon name the shell maps
    order: int = 100


@dataclass(frozen=True)
class ChannelDescriptor:
    name: str  # channel key ("lark"); also the WorkingSource value for inbound turns
    display_name: str
    transport: Transport = "socket"
    credential_schema: CredentialSchema = field(default_factory=CredentialSchema)
    # "pkg.mod:Class" references, resolved lazily
    trigger_ref: str = ""  # ChannelTriggerBase subclass (empty: credentials only)
    module_ref: str = ""  # ChannelModuleBase subclass
    credential_manager_ref: str = ""  # per-channel manager until 4d
    credential_read_method: str = "get"
    service_ref: str = ""  # module with do_bind / do_test_connection / do_unbind
    bind_takes: Literal["mgr", "db"] = "mgr"
    # What a bind CALL accepts for a manager-backed channel (the service's do_bind
    # keyword arguments — a pasted bind link, an owner e-mail to resolve, …). Empty
    # means the stored credential schema IS the bind input (plugin channels).
    bind_fields: tuple[CredentialField, ...] = ()
    has_bind: bool = True
    has_test: bool = True
    unbind_service: bool = False  # unbind goes through the service (lark) rather than the manager
    ui: Optional[ChannelUi] = None
    meta: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.name or not self.name.replace("_", "").isalnum() or self.name != self.name.lower():
            raise ValueError(f"channel name must be lowercase [a-z0-9_], got {self.name!r}")
        for ref in (self.trigger_ref, self.module_ref, self.credential_manager_ref):
            if ref and ref.count(":") != 1:
                raise ValueError(f"{self.name}: class reference must look like 'pkg.mod:Class', got {ref!r}")

    @property
    def has_inbound(self) -> bool:
        return bool(self.trigger_ref) and self.transport != "none"

    def resolve(self, ref: str) -> Any:
        module_path, _, attr = ref.partition(":")
        return getattr(importlib.import_module(module_path), attr)


__all__ = ["ChannelDescriptor", "ChannelUi", "CredentialField", "CredentialSchema", "FieldKind", "Transport"]
