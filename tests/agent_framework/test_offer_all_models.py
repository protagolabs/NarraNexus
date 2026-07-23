"""
@file_name: test_offer_all_models.py
@author:
@date: 2026-07-23
@description: The NARRANEXUS_OFFER_ALL_MODELS dev opt-in.

When a local developer sets NARRANEXUS_OFFER_ALL_MODELS truthy, an aggregator
provider (netmind / openrouter / yunwu) offers EVERY catalogued model instead
of only the probe-passing ones — so a machine with poor connectivity to the
aggregator (many spurious probe FAILs) can still select any model. Default off
keeps cloud + normal local installs on the pass-filtered list.
"""
import pytest

from xyz_agent_context.agent_framework import model_catalog, model_probe_ledger
from xyz_agent_context.agent_framework.model_probe_ledger import PASS, FAIL

FLAG = "NARRANEXUS_OFFER_ALL_MODELS"

# A ledger where each protocol drops some models: anthropic passes {a}, openai
# passes {a, b}; c fails both. The catalogue (all ids) is {a, b, c}.
_LEDGER = {
    "generated_at": None,
    "sources": {
        "netmind": {"models": {
            "a": {"openai": PASS, "anthropic": PASS},
            "b": {"openai": PASS, "anthropic": FAIL},
            "c": {"openai": FAIL, "anthropic": FAIL},
        }},
    },
}


@pytest.fixture
def stub_ledger(monkeypatch):
    monkeypatch.setattr(model_probe_ledger, "load_ledger", lambda: _LEDGER)


# ---------------------------------------------------------------------------
# all_ledger_models — the raw "every id" read
# ---------------------------------------------------------------------------

def test_all_ledger_models_returns_every_id_regardless_of_pass(stub_ledger):
    assert model_probe_ledger.all_ledger_models("netmind") == ["a", "b", "c"]


def test_all_ledger_models_system_pool_aliases_netmind(stub_ledger):
    assert model_probe_ledger.all_ledger_models("system_pool") == ["a", "b", "c"]


def test_all_ledger_models_unknown_source_is_empty(stub_ledger):
    assert model_probe_ledger.all_ledger_models("openrouter") == []


# ---------------------------------------------------------------------------
# offer_all_models_enabled — the env flag
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("val", ["1", "true", "TRUE", "yes", " Yes "])
def test_flag_truthy_spellings_on(monkeypatch, val):
    monkeypatch.setenv(FLAG, val)
    assert model_catalog.offer_all_models_enabled() is True


@pytest.mark.parametrize("val", ["0", "false", "no", "", "off"])
def test_flag_falsy_spellings_off(monkeypatch, val):
    monkeypatch.setenv(FLAG, val)
    assert model_catalog.offer_all_models_enabled() is False


def test_flag_unset_is_off(monkeypatch):
    monkeypatch.delenv(FLAG, raising=False)
    assert model_catalog.offer_all_models_enabled() is False


# ---------------------------------------------------------------------------
# get_default_models — filter vs full-catalogue behaviour
# ---------------------------------------------------------------------------

def test_flag_off_filters_by_pass(stub_ledger, monkeypatch):
    monkeypatch.delenv(FLAG, raising=False)
    # anthropic: only {a}; openai: {a, b}
    assert model_catalog.get_default_models("netmind", "anthropic") == ["a"]
    assert model_catalog.get_default_models("netmind", "openai") == ["a", "b"]


def test_flag_on_returns_all_for_both_protocols(stub_ledger, monkeypatch):
    monkeypatch.setenv(FLAG, "1")
    # Both protocol rows now offer the whole catalogue, incl. probe-failed c.
    assert model_catalog.get_default_models("netmind", "anthropic") == ["a", "b", "c"]
    assert model_catalog.get_default_models("netmind", "openai") == ["a", "b", "c"]


def test_flag_on_empty_ledger_falls_back_to_hardcoded(monkeypatch):
    # Ledger has nothing for netmind → even with the flag on we must not return
    # [] (which would leave the provider with no models); fall back to the
    # hardcoded defaults so a fresh checkout still works.
    monkeypatch.setattr(model_probe_ledger, "load_ledger",
                        lambda: {"generated_at": None, "sources": {}})
    monkeypatch.setenv(FLAG, "1")
    got = model_catalog.get_default_models("netmind", "anthropic")
    assert got  # non-empty
    assert "anthropic/claude-opus-4-8" in got  # the hardcoded default
