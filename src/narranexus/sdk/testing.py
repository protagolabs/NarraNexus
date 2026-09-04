"""
@file_name: testing.py
@author: Bin Liang
@date: 2026-09-03
@description: ``PluginTestHost`` — boot a minimal host around ONE plugin directory so its tests assert real registrations.

The host uses a throwaway plugin home, links the plugin in place, runs the
kernel's staged boot for the requested role on a fresh ``Registries``,
and exposes what a plugin test wants to look at: the registries, the
routes mounted on a FastAPI test app (auth-exempt for tests), the tables
the plugin registered, the hook registry, and an activator with an
in-memory settings store and event bus. No database is started; pass
``db_client`` to give ``ctx.db`` a real client.
"""
from __future__ import annotations

import asyncio
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from narranexus.contracts.settings import SettingsSchema
from narranexus.hosts.boot import BootReport, boot
from narranexus.kernel.events.bus import EventBus
from narranexus.kernel.plugins.activation import Activator
from narranexus.kernel.plugins.context import PluginContext, build_context
from narranexus.kernel.plugins.install import Installer, LocalSource
from narranexus.kernel.plugins.lifecycle import RegistryStore
from narranexus.kernel.plugins.manifest import Manifest
from narranexus.kernel.plugins.paths import ENV_PLUGIN_HOME
from narranexus.kernel.plugins.registries import Registries
from narranexus.kernel.plugins.services import ServiceLocator
from narranexus.kernel.settings import PluginSettings
from narranexus.kernel.settings.plugin_settings import MemorySettingsStore


@dataclass
class PluginTestHost:
    plugin_dir: Path
    home: Path
    role: str = "backend"
    host_version: str = "1.19.0"
    db_client: Any = None
    registries: Registries = field(default_factory=Registries)
    bus: EventBus = field(default_factory=EventBus)
    services: ServiceLocator = field(default_factory=ServiceLocator)
    settings_store: MemorySettingsStore = field(default_factory=MemorySettingsStore)
    report: BootReport | None = None
    tables: list[tuple[str, str]] = field(default_factory=list)
    _activator: Activator | None = None
    _store: RegistryStore | None = None
    _previous_home: str | None = None

    # ------------------------------------------------------------- setup

    def __enter__(self) -> "PluginTestHost":
        self._previous_home = os.environ.get(ENV_PLUGIN_HOME)
        self.home.mkdir(parents=True, exist_ok=True)
        os.environ[ENV_PLUGIN_HOME] = str(self.home)
        self._store = RegistryStore(path=self.home / "registry.json", lkg=self.home / "registry.lkg.json")
        Installer(store=self._store, host=self.host_version).install(LocalSource(self.plugin_dir, mode="link"))
        self._activator = Activator(context_factory=self._context_factory, on_crash=lambda pid, err: self._store.record_crash(pid, err))  # type: ignore[union-attr]
        self.report = boot(
            self.role,  # type: ignore[arg-type]
            registries=self.registries,
            cloud=False,
            host_version=self.host_version,
            activator=self._activator,
            register_table=lambda spec, owner: self.tables.append((spec.name, owner)),
            store=self._store,
        )
        self.report.mark_healthy()
        return self

    def __exit__(self, *exc: object) -> None:
        from narranexus.kernel.plugins.importer import plugin_finder, uninstall_synthetic_package

        if self._activator is not None:
            for pid in list(self.loaded):
                asyncio.run(self._activator.deactivate(pid))
        for pid in self.loaded:
            uninstall_synthetic_package(pid)
            plugin_finder().unregister_deps(pid)
        self.bus.close()
        if self._previous_home is None:
            os.environ.pop(ENV_PLUGIN_HOME, None)
        else:
            os.environ[ENV_PLUGIN_HOME] = self._previous_home

    def _context_factory(self, manifest: Manifest) -> PluginContext:
        schema = SettingsSchema()
        if "backend.settings" in manifest.provides:
            for entry in self.registries.registry_for("backend.settings").entries():
                if entry.owner == manifest.id:
                    schema = entry.factory()
        return build_context(
            plugin_id=manifest.id,
            version=manifest.version,
            path=self.plugin_dir,
            role=self.role,  # type: ignore[arg-type]
            host_version=self.host_version,
            registries=self.registries,
            provides=tuple(manifest.provides),
            settings=PluginSettings(manifest.id, schema, store=self.settings_store),
            db_client=self.db_client,
            bus=self.bus,
            services=self.services.scoped(manifest.id),
        )

    # ----------------------------------------------------------- queries

    @property
    def loaded(self) -> tuple[str, ...]:
        return self.report.user_plugin_ids if self.report else ()

    @property
    def plugin_id(self) -> str:
        assert self.report is not None
        if self.report.isolated:
            raise AssertionError(f"plugin isolated: {self.report.isolated}")
        return self.loaded[0]

    def registry(self, path: str) -> Any:
        return self.registries.registry_for(path)

    def names(self, path: str) -> tuple[str, ...]:
        return self.registries.registry_for(path).names()

    @property
    def hooks(self) -> Any:
        return self.registries.hooks

    async def activate(self) -> PluginContext:
        assert self._activator is not None
        result = await self._activator.activate_now(self.plugin_id)
        if result.error:
            raise AssertionError(f"activation failed: {result.error}")
        ctx = self._activator.context_of(self.plugin_id)
        assert ctx is not None
        return ctx

    def test_app(self) -> Any:
        """A FastAPI app with the plugin's routes mounted (no auth middleware; tests talk to the routes directly)."""
        from fastapi import FastAPI

        from narranexus.contracts.route import RouterSpec

        app = FastAPI()
        for entry in self.registries.registry_for("backend.routes").entries():
            spec = entry.factory()
            if isinstance(spec, RouterSpec):
                app.include_router(spec.router, prefix=spec.prefix)
        return app


__all__ = ["PluginTestHost"]
