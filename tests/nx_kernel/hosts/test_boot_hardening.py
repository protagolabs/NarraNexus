"""
@file_name: test_boot_hardening.py
@author: Bin Liang
@date: 2026-09-07
@description: boot() write-back discipline: inspect=True never writes (no boot marker, no state); a corrupt registry.json boots builtins; an isolated plugin leaves no partial registrations; a refused table counts as a crash; the last-known-good snapshot moves only on mark_healthy; the stage-2 deadline records "slow", never a crash.
"""
from __future__ import annotations

from pathlib import Path

from narranexus.contracts.settings import SettingsSchema  # noqa: F401 — the good symbol the bad plugin provides first
from narranexus.hosts.boot import boot
from narranexus.kernel.plugins.lifecycle import PluginRecord, RegistryStore
from narranexus.kernel.plugins.registries import Registries

from .conftest import make_plugin, register


def _record(path: Path) -> PluginRecord:
    return PluginRecord(path=str(path), installed_version="1.0.0")


def test_inspect_boot_writes_nothing(plugin_home: Path):
    make_plugin(plugin_home, "acme.ok")
    store = register(plugin_home, "acme.ok", plugin_home / "acme.ok")
    before = store.path.read_text()
    for _ in range(3):
        report = boot("backend", registries=Registries(), cloud=False, host_version="1.19.0", store=store, inspect=True)
        assert report.user_plugin_ids == ("acme.ok",)
    assert not (plugin_home / ".booting-backend").exists()
    assert store.path.read_text() == before  # no transitions, no crash counts
    assert not store.read().safe_mode


def test_corrupt_registry_boots_builtins_only(plugin_home: Path):
    store = RegistryStore(path=plugin_home / "registry.json", lkg=plugin_home / "registry.lkg.json")
    store.path.write_text("{")
    report = boot("backend", registries=Registries(), cloud=False, host_version="1.19.0", store=store)
    assert report.builtins is not None and report.builtins.loaded
    assert report.user_plugin_ids == ()


def test_isolated_plugin_leaves_no_partial_registrations(plugin_home: Path):
    body = (
        "from narranexus.contracts.settings import SettingsSchema, SettingField\n"
        "from narranexus.kernel.plugins.registry import Contribution\n"
        "SETTINGS = Contribution('schema', lambda: SettingsSchema(fields=(SettingField('token', 'string'),)))\n"
    )
    make_plugin(
        plugin_home, "acme.half", body=body,
        extra={"provides": {"backend.settings": ["nxplugins.acme_half:SETTINGS"], "backend.routes": ["nxplugins.acme_half:NOPE"]}, "api": {"settings": 0, "route": 0}},
    )
    store = register(plugin_home, "acme.half", plugin_home / "acme.half")
    registries = Registries()
    report = boot("backend", registries=registries, cloud=False, host_version="1.19.0", store=store)
    assert "acme.half" in report.isolated
    assert "acme.half" not in {e.owner for e in registries.registry_for("backend.settings").entries()}


def test_refused_table_counts_as_a_crash(plugin_home: Path):
    body = (
        "from narranexus.contracts.table import ColumnSpec, TableSpec\n"
        "from narranexus.kernel.plugins.registry import Contribution\n"
        "TABLES = Contribution('items', lambda: TableSpec('ext_acme_tables__items', (ColumnSpec('id', 'TEXT', 'VARCHAR(64)', primary_key=True),)))\n"
    )
    make_plugin(plugin_home, "acme.tables", body=body, extra={"provides": {"backend.tables": ["nxplugins.acme_tables:TABLES"]}, "api": {"table": 0}})
    store = register(plugin_home, "acme.tables", plugin_home / "acme.tables")

    def refuse(spec, owner):
        raise ValueError("migration refused this table")

    report = boot("backend", registries=Registries(), cloud=False, host_version="1.19.0", store=store, register_table=refuse)
    assert "acme.tables" in report.isolated
    assert store.read().plugins["acme.tables"].crash_count == 1


def test_last_known_good_moves_only_when_the_host_is_healthy(plugin_home: Path):
    make_plugin(plugin_home, "acme.ok")
    store = register(plugin_home, "acme.ok", plugin_home / "acme.ok")
    report = boot("backend", registries=Registries(), cloud=False, host_version="1.19.0", store=store)
    assert store.read().plugins["acme.ok"].state == "enabled"
    assert store.read_lkg() is None  # the boot wrote, but nothing proved it healthy yet
    report.mark_healthy()
    lkg = store.read_lkg()
    assert lkg is not None and lkg.plugins["acme.ok"].state == "enabled"
    assert not (plugin_home / ".booting-backend").exists()


def test_stage2_deadline_records_slow_not_crashed(plugin_home: Path):
    make_plugin(plugin_home, "acme.ok")
    store = register(plugin_home, "acme.ok", plugin_home / "acme.ok")
    report = boot("backend", registries=Registries(), cloud=False, host_version="1.19.0", store=store, stage2_deadline_s=-1.0)
    assert report.user_plugin_ids == () and "acme.ok" in report.slow and report.isolated == {}
    rec = store.read().plugins["acme.ok"]
    assert rec.crash_count == 0 and rec.state == "slow" and rec.enabled
