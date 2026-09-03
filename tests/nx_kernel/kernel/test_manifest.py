"""
@file_name: test_manifest.py
@author: Bin Liang
@date: 2026-09-03
@description: Manifest validation — shape, slot-awareness, arity, builtin prefix, api/minAppVersion gates.
"""
from __future__ import annotations

import json

import pytest

from narranexus.contracts import ManifestError
from narranexus.kernel.plugins.manifest import (
    Manifest,
    derive_activation_events,
    load_manifest,
    parse_manifest,
)
from narranexus.kernel.plugins.slots import Slot, SlotTree, build_kernel_slot_tree


def _tree() -> SlotTree:
    tree = build_kernel_slot_tree()
    tree.declare(Slot("ui.pages", "many", "narranexus.contracts.ui:Page", "builtin.ui"))
    tree.declare(Slot("ui.panels", "many", "narranexus.contracts.ui:Panel", "builtin.ui"))
    return tree


def _base(**overrides) -> dict:
    data = {
        "id": "acme.weather",
        "version": "1.2.0",
        "displayName": "Weather",
        "provides": {"model.providers": ["backend.provider:WeatherDriver"]},
    }
    data.update(overrides)
    return data


def test_minimal_manifest_parses_and_exposes_helpers():
    m = parse_manifest(_base(), tree=_tree())
    assert m.id == "acme.weather" and m.display_name == "Weather"
    assert not m.is_builtin and str(m.semantic_version) == "1.2.0"
    assert m.provided_slots() == ("model.providers",)
    assert m.quality == "bronze" and m.install.deps == "eager"
    assert derive_activation_events(m) == ("onStartup",)


def test_full_manifest_round_trip_including_declares_and_permissions():
    data = _base(
        publisher={"name": "acme", "url": "https://github.com/acme"},
        license="MIT",
        minAppVersion="1.19.0",
        api={"provider": 0},
        dependencies={"builtin.providers": ">=1.0"},
        afterDependencies=["builtin.ui"],
        hosts=["backend", "frontend"],
        backend={"package": "backend", "pip": ["httpx>=0.27"], "activate": True},
        frontend={"entry": "frontend/dist/plugin.js", "locales": "frontend/locales"},
        provides={
            "model.providers": ["backend.provider:WeatherDriver"],
            "ui.pages": ["frontend:WeatherPage"],
            "acme.weather.sources": ["backend.sources:Default"],
        },
        declares={"acme.weather.sources": {"arity": "many", "contract": "backend.contracts:WeatherSource"}},
        permissions={"network": ["api.weather.com"], "subprocess": False, "env": ["WEATHER_KEY"]},
        install={"deps": "on_demand"},
        size={"backend_deps_mb": 12, "frontend_kb": 340},
        quality="silver",
    )
    m = parse_manifest(data, tree=_tree(), host_version="1.19.5")
    assert m.hosts == ("backend", "frontend") and m.backend.activate is True
    assert m.permissions.network == ("api.weather.com",)
    (declared,) = m.declared_slots()
    assert (declared.path, declared.arity, declared.owner) == ("acme.weather.sources", "many", "acme.weather")
    assert derive_activation_events(m) == ("onStartup", "onPage:acme.weather")


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"id": "weather"}, "id must be"),
        ({"id": "Acme.Weather"}, "id must be"),
        ({"version": "v1"}, "version"),
        ({"minAppVersion": "one"}, "minAppVersion"),
        ({"dependencies": {"builtin.providers": ">= banana"}}, "dependencies"),
        ({"quality": "platinum"}, "quality"),
        ({"unknownKey": 1}, "unknownKey"),
        ({"provides": {"model.providers": "backend.provider:WeatherDriver"}}, "many-arity slot; give a list"),
        ({"provides": {"turn.act.framework": ["backend:Loop"]}}, "one-arity slot; give a single symbol"),
        ({"provides": {"model.providers": ["not a symbol"]}}, "module.path:Symbol"),
        ({"provides": {"nope.slot": ["backend:X"]}}, "not a declared slot"),
        ({"provides": {"kernel.auth": "backend:Sso"}}, "distribution-only"),
        ({"api": {"provider": 99}}, "wants 99"),
        ({"api": {"unicorn": 0}}, "not a contract kind"),
        ({"redeclares": ["turn.act"]}, "descendant of a slot this plugin provides"),
        ({"declares": {"Bad Path": {"arity": "one", "contract": "x:Y"}}}, "declares"),
        ({"declares": {"acme.x": {"arity": "one", "contract": "nope"}}}, "module.path:Symbol"),
    ],
)
def test_invalid_manifests_fail_loud_and_name_the_field(overrides, message):
    with pytest.raises(ManifestError, match=message):
        parse_manifest(_base(**overrides), tree=_tree())


def test_builtin_prefix_is_reserved_unless_allowed():
    data = _base(id="builtin.weather")
    with pytest.raises(ManifestError, match="reserved"):
        parse_manifest(data, tree=_tree())
    assert parse_manifest(data, tree=_tree(), allow_builtin=True).is_builtin


def test_distribution_only_manifest_may_provide_a_distribution_only_slot():
    data = _base(provides={"kernel.auth": "backend.sso:Provider"}, distributionOnly=True)
    m = parse_manifest(data, tree=_tree())
    assert m.distribution_only and m.provides["kernel.auth"] == "backend.sso:Provider"


def test_min_app_version_gate():
    data = _base(minAppVersion="2.0.0")
    with pytest.raises(ManifestError, match="minAppVersion 2.0.0 exceeds host 1.19.0"):
        parse_manifest(data, tree=_tree(), host_version="1.19.0")
    parse_manifest(data, tree=_tree(), host_version="2.0.0")


def test_redeclares_must_be_under_a_provided_composite_slot():
    tree = _tree()
    data = _base(provides={"turn.act": "backend.act:Strategy"}, redeclares=["turn.act.framework"])
    m = parse_manifest(data, tree=tree)
    assert m.redeclares == ("turn.act.framework",)
    with pytest.raises(ManifestError, match="not a known slot"):
        parse_manifest(_base(provides={"turn.act": "backend.act:Strategy"}, redeclares=["turn.act.nope"]), tree=tree)


def test_load_manifest_from_disk_reports_unreadable_or_bad_json(tmp_path):
    path = tmp_path / "narranexus-plugin.json"
    with pytest.raises(ManifestError, match="cannot read manifest"):
        load_manifest(path, tree=_tree())
    path.write_text("{not json", encoding="utf-8")
    with pytest.raises(ManifestError, match="cannot read manifest"):
        load_manifest(path, tree=_tree())
    path.write_text(json.dumps(_base()), encoding="utf-8")
    assert load_manifest(path, tree=_tree()).id == "acme.weather"


def test_manifest_is_immutable():
    m = parse_manifest(_base(), tree=_tree())
    with pytest.raises(Exception):
        m.version = "9.9.9"  # type: ignore[misc]
    assert isinstance(m, Manifest)
