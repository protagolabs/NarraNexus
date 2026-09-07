"""
@file_name: __init__.py
@author: Bin Liang
@date: 2026-09-03
@description: ``narranexus.sdk`` — what a plugin author imports: the contracts, the registration primitives, and a test host.

Plugins import from here (or from ``narranexus.contracts`` directly); never
from ``narranexus.kernel`` internals. The names are re-exports, so the SDK
is a stable door, not a second copy. ``PluginTestHost`` (``testing``) boots
a minimal host around one plugin directory for its tests.
"""
from narranexus.contracts import API_VERSIONS, Disposable, DisposableStack, PluginError
from narranexus.contracts.agent.capability import ContextProvider
from narranexus.contracts.agent.pipeline import PipelineProfile, StageStrategy
from narranexus.contracts.agent.stages import Stage
from narranexus.contracts.bundle import BundleSpec
from narranexus.contracts.channel import ChannelDescriptor, ChannelUi, CredentialField, CredentialSchema
from narranexus.contracts.mcp_server import McpServerSpec
from narranexus.contracts.route import RouterSpec, plugin_route_prefix
from narranexus.contracts.settings import SettingField, SettingsSchema
from narranexus.contracts.skill import SkillSpec
from narranexus.contracts.table import ColumnSpec, IndexSpec, MigrationSpec, TableSpec, table_prefix_for
from narranexus.contracts.tool import ToolProvider, ToolSpec
from narranexus.contracts.trigger import TriggerSpec
from narranexus.contracts.ui import Theme
from narranexus.contracts.worker import WorkerHandle, WorkerSpec
from narranexus.kernel.plugins.context import PluginContext
from narranexus.kernel.plugins.hooks import HookImplSpec, hookimpl
from narranexus.kernel.plugins.registry import Contribution
from narranexus.kernel.plugins.services import ServiceRef

__all__ = [
    "API_VERSIONS",
    "BundleSpec",
    "ChannelDescriptor",
    "ChannelUi",
    "ColumnSpec",
    "ContextProvider",
    "Contribution",
    "CredentialField",
    "CredentialSchema",
    "Disposable",
    "DisposableStack",
    "HookImplSpec",
    "IndexSpec",
    "McpServerSpec",
    "MigrationSpec",
    "PipelineProfile",
    "PluginContext",
    "PluginError",
    "RouterSpec",
    "ServiceRef",
    "SettingField",
    "SettingsSchema",
    "SkillSpec",
    "Stage",
    "StageStrategy",
    "TableSpec",
    "Theme",
    "ToolProvider",
    "ToolSpec",
    "TriggerSpec",
    "WorkerHandle",
    "WorkerSpec",
    "hookimpl",
    "plugin_route_prefix",
    "table_prefix_for",
]
