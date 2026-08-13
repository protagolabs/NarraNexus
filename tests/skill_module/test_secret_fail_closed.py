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


def test_configured_env_var_names_excludes_undecryptable(module, key):
    box = sb.get_secret_box()
    stranded = Fernet(Fernet.generate_key()).encrypt(b"lost").decode("ascii")
    _make_skill(module, "myskill", {"GOOD": box.encrypt("real-value"), "BAD": stranded})

    configured = module.get_configured_env_var_names("myskill")
    assert "GOOD" in configured
    assert "BAD" not in configured  # drives env_configured=False → UI prompts


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
