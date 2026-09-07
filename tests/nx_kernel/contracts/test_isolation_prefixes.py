"""
@file_name: test_isolation_prefixes.py
@author: Bin Liang
@date: 2026-09-07
@description: The per-plugin isolation prefixes (table ext_<id>__, env NXP_<ID>__<KEY>) are injective — a sibling plugin id can never be a prefix of another's tables or settings — because plugin ids never contain '__' or end in '_'; and api[kind] uses floor..current semantics so a bump opens a deprecation window instead of breaking every plugin at once.
"""
from __future__ import annotations

import pytest

from narranexus.contracts import API_VERSIONS, MIN_SUPPORTED_VERSIONS, ManifestError, Stability, STABILITY
from narranexus.contracts.settings import SettingField, SettingsSchema
from narranexus.contracts.table import table_prefix_for
from narranexus.kernel.plugins.manifest import PLUGIN_ID_RE


def test_table_prefix_of_a_sibling_id_is_never_a_prefix():
    a, b = table_prefix_for("acme.weather"), table_prefix_for("acme.weather_x")
    assert a == "ext_acme_weather__" and b == "ext_acme_weather_x__"
    assert not (b + "items").startswith(a)
    assert not (table_prefix_for("acme.x_y") + "t").startswith(table_prefix_for("acme.x"))


def test_settings_env_names_cannot_collide_across_plugins():
    schema = SettingsSchema(fields={"c_d": SettingField("string"), "d": SettingField("string")})
    assert schema.env_name("a.b", "c_d") == "NXP_A_B__C_D"
    assert schema.env_name("a.b_c", "d") == "NXP_A_B_C__D"


@pytest.mark.parametrize("bad", ["acme.a__b", "acme.a_", "acme._a", "acme..b", "Acme.b", "acme"])
def test_plugin_ids_that_would_break_injectivity_are_rejected(bad):
    assert PLUGIN_ID_RE.match(bad) is None


@pytest.mark.parametrize("good", ["acme.weather", "acme.weather_x", "builtin.channels.lark", "acme-corp.my-plugin"])
def test_plain_plugin_ids_are_accepted(good):
    assert PLUGIN_ID_RE.match(good)


def test_api_version_floor_semantics(monkeypatch):
    from narranexus.kernel.plugins import manifest as m

    class _M:
        id = "acme.x"
        api = {"provider": 0}

    monkeypatch.setitem(API_VERSIONS, "provider", 2)
    monkeypatch.setitem(MIN_SUPPORTED_VERSIONS, "provider", 1)
    with pytest.raises(ManifestError, match="no longer supported"):
        m._check_api_versions(_M())
    _M.api = {"provider": 1}
    m._check_api_versions(_M())  # inside the window
    _M.api = {"provider": 2}
    m._check_api_versions(_M())
    _M.api = {"provider": 3}
    with pytest.raises(ManifestError, match="upgrade the host"):
        m._check_api_versions(_M())


def test_stability_is_declared_per_kind():
    assert set(STABILITY) == set(API_VERSIONS) == set(MIN_SUPPORTED_VERSIONS)
    assert all(isinstance(s, Stability) for s in STABILITY.values())
    assert all(MIN_SUPPORTED_VERSIONS[k] <= API_VERSIONS[k] for k in API_VERSIONS)
