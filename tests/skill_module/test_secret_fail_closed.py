"""
@file_name: test_secret_fail_closed.py
@date: 2026-08-13
@description: Skill env-config fail-closed behaviour (2026-08-01 incident).

When a stored credential was encrypted under a key that rotated/was lost, it
must NOT be injected as ciphertext into the skill's runtime env (which made the
skill run with garbage and fail opaquely). It is skipped, logged, and the skill
reports env_configured=False so the UI prompts a re-enter.
"""
from __future__ import annotations

import json

import pytest
from cryptography.fernet import Fernet

from xyz_agent_context.module.skill_module.skill_module import SkillModule
import xyz_agent_context.marketplace._skill_marketplace_impl.secret_box as sb


@pytest.fixture
def key(monkeypatch):
    """Pin a known SecretBox key and reset the process-wide cache."""
    k = Fernet.generate_key().decode("ascii")
    monkeypatch.setenv("SKILL_SECRETS_KEY", k)
    monkeypatch.setattr(sb, "_default_box", None)
    return k


@pytest.fixture
def module(tmp_path):
    m = SkillModule(agent_id="agent_test", user_id="test_user")
    m.skills_dir = tmp_path / "skills"
    m.skills_dir.mkdir(parents=True, exist_ok=True)
    return m


def _make_skill(module, name, env_config):
    d = module.skills_dir / name
    d.mkdir(parents=True, exist_ok=True)
    (d / "SKILL.md").write_text(f"---\nname: {name}\n---\n# {name}\n")
    (d / ".skill_meta.json").write_text(json.dumps({"name": name, "env_config": env_config}))
    return d


def test_get_all_skill_env_vars_skips_undecryptable(module, key):
    box = sb.get_secret_box()
    stranded = Fernet(Fernet.generate_key()).encrypt(b"lost").decode("ascii")
    _make_skill(module, "myskill", {"GOOD": box.encrypt("real-value"), "BAD": stranded})

    env = module.get_all_skill_env_vars()
    assert env.get("GOOD") == "real-value"
    assert "BAD" not in env  # ciphertext never injected — fail closed


def test_configured_env_var_names_excludes_undecryptable(key):
    # The single source of truth for "is this var configured": present + it
    # decrypts. A ciphertext under a lost key is NOT configured → drives
    # env_configured False everywhere (list/detail/MCP/hook/install).
    from xyz_agent_context.module.skill_module.skill_module import (
        configured_env_var_names,
    )

    box = sb.get_secret_box()
    stranded = Fernet(Fernet.generate_key()).encrypt(b"lost").decode("ascii")
    env_config = {"GOOD": box.encrypt("real-value"), "BAD": stranded, "EMPTY": ""}

    configured = configured_env_var_names(env_config)
    assert configured == {"GOOD"}  # BAD undecryptable, EMPTY blank


def test_env_configured_reflects_decryptability_incl_disabled(module, key):
    # env_configured is computed at the SkillModule source (_parse_skill_md),
    # so it is truthful for ENABLED and DISABLED skills alike (the helper takes
    # a raw env_config dict, never a name → no enabled-only path resolution).
    import json

    box = sb.get_secret_box()
    stranded = Fernet(Fernet.generate_key()).encrypt(b"lost").decode("ascii")

    # Enabled skill, credential intact → configured.
    good = module.skills_dir / "good"
    good.mkdir(parents=True)
    (good / "SKILL.md").write_text("---\nname: good\nrequires:\n  env:\n    - API_KEY\n---\n# good\n")
    (good / ".skill_meta.json").write_text(
        json.dumps({"name": "good", "requires": {"env": ["API_KEY"]},
                    "env_config": {"API_KEY": box.encrypt("v")}})
    )
    # Disabled skill, credential intact → must STAY configured (regression:
    # the old downgrade pass resolved by name and only saw enabled dirs, so it
    # flagged every disabled skill as needing re-entry).
    disabled = module.skills_dir / ".disabled" / "kept"
    disabled.mkdir(parents=True)
    (disabled / "SKILL.md").write_text("---\nname: kept\nrequires:\n  env:\n    - API_KEY\n---\n# kept\n")
    (disabled / ".skill_meta.json").write_text(
        json.dumps({"name": "kept", "requires": {"env": ["API_KEY"]},
                    "env_config": {"API_KEY": box.encrypt("v")}})
    )
    # Enabled skill whose credential is stranded → NOT configured.
    bad = module.skills_dir / "bad"
    bad.mkdir(parents=True)
    (bad / "SKILL.md").write_text("---\nname: bad\nrequires:\n  env:\n    - API_KEY\n---\n# bad\n")
    (bad / ".skill_meta.json").write_text(
        json.dumps({"name": "bad", "requires": {"env": ["API_KEY"]},
                    "env_config": {"API_KEY": stranded}})
    )

    by_name = {s.name: s for s in module.list_skills(include_disabled=True)}
    assert by_name["good"].env_configured is True
    assert by_name["kept"].env_configured is True  # disabled + intact → still green
    assert by_name["bad"].env_configured is False  # stranded → prompts re-enter


def test_needs_rewrite_migration_skipped_when_any_value_undecryptable(module, key):
    # A legacy-base64 value would normally be re-persisted (migrated). If the
    # SAME skill also has an undecryptable value, we must NOT rewrite the meta
    # — that would overwrite the still-encrypted ciphertext of the failed one
    # and destroy the last chance to recover it with the old key.
    import base64

    box = sb.get_secret_box()
    legacy = base64.b64encode(b"legacy-val").decode("ascii")
    stranded = Fernet(Fernet.generate_key()).encrypt(b"lost").decode("ascii")
    d = _make_skill(module, "myskill", {"LEG": legacy, "BAD": stranded})

    module.get_all_skill_env_vars()
    meta = json.loads((d / ".skill_meta.json").read_text())
    # LEG NOT migrated to Fernet — meta untouched because BAD failed.
    assert meta["env_config"]["LEG"] == legacy
    assert meta["env_config"]["BAD"] == stranded
