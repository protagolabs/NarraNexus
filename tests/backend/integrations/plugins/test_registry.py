"""
@file_name: test_registry.py
@author: NarraNexus
@date: 2026-08-28
@description: Tests for the two-plugin registry — pins must track their
              single sources of truth, not be re-typed literals.
"""
from __future__ import annotations

from xyz_agent_context.agent_framework.adapters.claude.cli_binary import PINNED_CLI_VERSION

from backend.integrations.plugins.registry import PLUGIN_SPECS


def test_registry_has_exactly_claude_and_codex():
    assert set(PLUGIN_SPECS) == {"claude_code", "codex_cli"}


def test_claude_code_spec_components():
    spec = PLUGIN_SPECS["claude_code"]
    assert spec.probe_package == "claude_agent_sdk"
    assert spec.user_version_source == "npm_cli"
    kinds = [c.kind for c in spec.components]
    assert kinds == ["pip", "npm"]

    pip_component = spec.components[0]
    npm_component = spec.components[1]
    assert pip_component.requirement == "claude-agent-sdk==0.1.43"
    # The npm requirement's version must come FROM the CLI binary pin, not a
    # re-typed literal, so bumping PINNED_CLI_VERSION alone keeps them in sync.
    assert npm_component.requirement == f"@anthropic-ai/claude-code@{PINNED_CLI_VERSION}"


def test_codex_cli_spec_components():
    spec = PLUGIN_SPECS["codex_cli"]
    assert spec.probe_package == "openai_codex"
    assert spec.user_version_source == "pip_pkg"
    assert len(spec.components) == 1
    assert spec.components[0].kind == "pip"
    assert spec.components[0].requirement == "openai-codex==0.1.0b3"


def test_every_spec_has_a_size_hint():
    for spec in PLUGIN_SPECS.values():
        assert spec.size_hint
        assert isinstance(spec.size_hint, str)


def test_plugin_id_equals_framework_name_and_dict_key():
    """The pyenv install location is keyed on spec.id while framework_installed
    keys on the framework name (plugin_paths.plugin_pyenv(name)). Those must be
    the same string, or a plugin installs into pyenv/<id>/ while availability
    probes pyenv/<framework_name>/ and reports 'not installed' forever. This
    turns that docstring-only contract into a guard (same shape as
    test_plugins_extra_lockstep / test_claude_cli_pin)."""
    from backend.integrations.plugins.registry import PLUGIN_SPECS

    for key, spec in PLUGIN_SPECS.items():
        assert key == spec.id == spec.framework_name, (
            f"plugin key/id/framework_name diverge: key={key!r} id={spec.id!r} "
            f"framework_name={spec.framework_name!r} — install location and "
            f"availability probe would key on different dirs"
        )


def test_pip_pins_match_uv_lock():
    """I7 guard: the registry's exact pip pins (claude-agent-sdk==X /
    openai-codex==Y) are a hand-written copy of what uv.lock resolves for the
    plugins extra. If a `uv lock --upgrade` bumps one and the registry is not
    updated in step, cloud base and a local plugin install land on different
    versions while both report 'installed'. Parse uv.lock and assert they agree
    (same shape as test_claude_cli_pin.py / test_plugins_extra_lockstep.py)."""
    import re
    import tomllib
    from pathlib import Path

    from packaging.version import Version

    from backend.integrations.plugins.registry import PLUGIN_SPECS

    repo = Path(__file__).resolve().parents[4]
    lock = tomllib.loads((repo / "uv.lock").read_text(encoding="utf-8"))
    locked = {pkg["name"]: pkg["version"] for pkg in lock.get("package", [])}

    # {distribution name -> pinned version} from every pip component.
    pinned: dict[str, str] = {}
    for spec in PLUGIN_SPECS.values():
        for comp in spec.components:
            if comp.kind != "pip":
                continue
            m = re.match(r"^([A-Za-z0-9_.\-]+)==(.+)$", comp.requirement)
            assert m, f"pip requirement not an exact pin: {comp.requirement!r}"
            pinned[m.group(1)] = m.group(2)

    assert pinned, "no pip pins found in PLUGIN_SPECS"
    for name, ver in pinned.items():
        assert name in locked, f"{name} pinned in registry but absent from uv.lock"
        assert Version(locked[name]) == Version(ver), (
            f"{name}: registry pins {ver} but uv.lock resolves {locked[name]} — "
            f"cloud base and local plugin install would diverge; bump the registry "
            f"pin in step with `uv lock`"
        )
