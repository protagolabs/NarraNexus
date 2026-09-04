"""
@file_name: plugins_boot.py
@author: Bin Liang
@date: 2026-09-03
@description: The backend process's plugin boot: wires the kernel boot sequence to this host's db, bus, settings store and activator.

``backend.main`` calls ``boot_backend_plugins()`` once in its lifespan,
before ``auto_migrate`` (plugin tables must exist even for inactive
plugins) and before routes are served; ``fire_startup()`` then activates
``onStartup`` plugins against the live process and ``mark_healthy`` clears
the boot marker. ``HOST_BUS`` / ``HOST_SERVICES`` are the backend's
process-wide event bus and service locator.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from loguru import logger

from narranexus.hosts.boot import BootReport, boot
from narranexus.kernel.deployment import is_cloud_mode
from narranexus.kernel.events.bus import EventBus
from narranexus.kernel.plugins.activation import Activator
from narranexus.kernel.plugins.compat import host_version
from narranexus.kernel.plugins.context import PluginContext, build_context
from narranexus.kernel.plugins.lifecycle import RegistryStore
from narranexus.kernel.plugins.manifest import Manifest
from narranexus.kernel.plugins.paths import registry_path
from narranexus.kernel.plugins.registries import KERNEL_REGISTRIES
from narranexus.kernel.plugins.services import HOST_VERSION
from narranexus.kernel.settings import PluginSettings
from narranexus.kernel.settings.plugin_settings import MemorySettingsStore

from xyz_agent_context.utils.db.schema_registry import register_table

HOST_BUS = EventBus()
# The backend process locator is the kernel registries' one (builtins expose
# their services there at import; user plugins through PluginContext.services).
HOST_SERVICES = KERNEL_REGISTRIES.services
_ACTIVATOR: Activator | None = None


def registry_store() -> RegistryStore:
    """Fresh each call so a relocated plugin home (tests) is honoured."""
    return RegistryStore(path=registry_path())


def _settings_for(manifest: Manifest) -> PluginSettings:
    from narranexus.contracts.settings import SettingsSchema

    schema = SettingsSchema()
    if "backend.settings" in manifest.provides:
        try:
            for entry in KERNEL_REGISTRIES.registry_for("backend.settings").entries():
                if entry.owner == manifest.id:
                    schema = entry.factory()
                    break
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"[plugins] {manifest.id}: settings schema unavailable: {exc}")
    try:
        from xyz_agent_context.utils.db.db_factory import get_db_client_sync
        from xyz_agent_context.utils.db.plugin_settings_store import DbSettingsStore

        store: Any = DbSettingsStore(get_db_client_sync())
    except Exception as exc:  # noqa: BLE001 — settings still work from env/defaults
        logger.warning(f"[plugins] {manifest.id}: settings store unavailable, using memory: {exc}")
        store = MemorySettingsStore()
    return PluginSettings(manifest.id, schema, store=store)


def _context_factory(manifest: Manifest) -> PluginContext:
    from xyz_agent_context.utils.db.db_factory import get_db_client_sync

    rec = registry_store().read().plugins.get(manifest.id)
    path = Path(rec.path) if rec else Path(".")
    return build_context(
        plugin_id=manifest.id,
        version=manifest.version,
        path=path,
        role="backend",
        host_version=host_version(),
        registries=KERNEL_REGISTRIES,
        provides=tuple(manifest.provides),
        settings=_settings_for(manifest),
        db_client=get_db_client_sync(),
        bus=HOST_BUS,
        services=HOST_SERVICES.scoped(manifest.id),
    )


def activator() -> Activator:
    global _ACTIVATOR
    if _ACTIVATOR is None:
        _ACTIVATOR = Activator(
            context_factory=_context_factory,
            on_crash=lambda pid, err: registry_store().record_crash(pid, err),
        )
    return _ACTIVATOR


def boot_backend_plugins() -> BootReport:
    if HOST_SERVICES.try_require(HOST_VERSION) is None:
        HOST_SERVICES.expose(HOST_VERSION, host_version(), owner="builtin.kernel")
    if KERNEL_REGISTRIES.frozen:
        # Lifespan ran more than once in this process (tests build several
        # TestClients); the registries are already populated and frozen.
        return BootReport(role="backend")
    return boot(
        "backend",
        registries=KERNEL_REGISTRIES,
        cloud=is_cloud_mode(),
        host_version=host_version(),
        activator=activator(),
        register_table=register_table,
        store=registry_store(),
    )


async def fire_startup() -> None:
    await activator().fire("onStartup")


__all__ = ["HOST_BUS", "HOST_SERVICES", "activator", "boot_backend_plugins", "fire_startup", "registry_store"]
