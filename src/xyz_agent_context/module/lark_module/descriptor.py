"""
@file_name: descriptor.py
@author: Bin Liang
@date: 2026-09-04
@description: builtin.channels.lark's ChannelDescriptor — the channel as one record for the ``ingress.channels`` registry.

Everything the platform used to know about Lark from six scattered
tables (trigger map, MODULE_MAP, the data-access CHANNELS table, the
WorkingSource member, the frontend row) now reads from this descriptor.
Class references are strings so registering it never imports the SDK.
"""
from __future__ import annotations

from narranexus.contracts.channel import ChannelDescriptor, ChannelUi, CredentialField, CredentialSchema
from narranexus.kernel.plugins.registry import Contribution
from xyz_agent_context.schema.hook_schema import WorkingSource

_MOD = "xyz_agent_context.module"

# The channel's working source (and TriggerType) — registered here, by the channel itself,
# so the platform holds no channel-name table. Imported first by the package.
SOURCE = WorkingSource.register("lark")

DESCRIPTOR = ChannelDescriptor(
    name="lark",
    display_name="Lark",
    transport="socket",
    credential_schema=CredentialSchema(
        fields=(
            CredentialField("app_id", "string", label="App ID", required=True, public=True),
            CredentialField("app_secret", "secret", label="App Secret", required=True, public=False),
            CredentialField("brand", "select", label="Brand", required=False, public=True),
        ),
        supports_test=False,
        external_id_field="app_id",
    ),
    trigger_ref=f"{_MOD}.lark_module.lark_trigger:LarkTrigger",
    module_ref=f"{_MOD}.lark_module.lark_module:LarkModule",
    credential_manager_ref=f"{_MOD}.lark_module._lark_credential_manager:LarkCredentialManager",
    credential_read_method="get_credential",
    service_ref=f"{_MOD}.lark_module._lark_service",
    bind_takes="mgr",
    bind_fields=(
        CredentialField("app_id", "string", label="App ID", required=True),
        CredentialField("app_secret", "secret", label="App Secret", required=True),
        CredentialField("brand", "select", label="Brand", required=True, options=("feishu", "lark")),
        CredentialField("owner_email", "string", label="Owner e-mail", required=False),
    ),
    has_bind=True,
    has_test=False,
    unbind_service=True,
    meta={
        "storage": "generic",  # 4d: the manager persists in channel_credentials
        # Every agent gets a LarkModule instance at creation (it may bind a Feishu bot later).
        "agent_instance": {
            "description": "Lark/Feishu integration: contacts, messages, documents, calendar, tasks",
            "keywords": ["lark", "feishu", "im", "messaging", "document", "calendar"],
            "topic_hint": "Lark/Feishu bot operations and IM interactions",
        },
    },
    ui=ChannelUi(label="Lark / Feishu", icon="message-square", order=10),
)

CHANNEL = (Contribution("lark", lambda: DESCRIPTOR),)

__all__ = ["CHANNEL", "DESCRIPTOR", "SOURCE"]
