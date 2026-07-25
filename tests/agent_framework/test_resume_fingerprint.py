"""
@file_name: test_resume_fingerprint.py
@author:
@date: 2026-07-24
@description: ClaudeConfig.resume_fingerprint() — identity of the CLI
session store a config resolves to. Stability (same config → same
fingerprint across calls) and divergence (any component change — auth
kind, endpoint, config dir, model — → different fingerprint, forcing a
cold start on the stored handle).
"""
from xyz_agent_context.agent_framework.api_config import ClaudeConfig


def _cfg(**overrides) -> ClaudeConfig:
    base = dict(
        api_key="sk-test",
        base_url="https://api.netmind.ai/inference-api/agents/v1",
        model="claude-sonnet-4-5",
        auth_type="bearer_token",
    )
    base.update(overrides)
    return ClaudeConfig(**base)


def test_fingerprint_is_stable_for_identical_config():
    assert _cfg().resume_fingerprint() == _cfg().resume_fingerprint()


def test_fingerprint_is_16_hex_chars():
    fp = _cfg().resume_fingerprint()
    assert len(fp) == 16
    assert all(c in "0123456789abcdef" for c in fp)


def test_fingerprint_ignores_api_key_rotation():
    # Key rotation on the SAME provider/endpoint must not invalidate the
    # handle — the key is deliberately NOT a fingerprint component.
    assert _cfg(api_key="sk-old").resume_fingerprint() == _cfg(api_key="sk-new").resume_fingerprint()


def test_fingerprint_diverges_on_auth_type():
    # bearer vs api_key share the keyed config dir, so the divergence here
    # proves auth_type itself is a component.
    assert _cfg(auth_type="bearer_token").resume_fingerprint() != _cfg(auth_type="api_key").resume_fingerprint()


def test_fingerprint_diverges_oauth_vs_keyed():
    # oauth flips BOTH auth_type and the config dir (to_cli_env's
    # CLAUDE_CONFIG_DIR branch) — must never match a keyed fingerprint.
    assert _cfg(auth_type="oauth").resume_fingerprint() != _cfg(auth_type="bearer_token").resume_fingerprint()


def test_fingerprint_diverges_on_base_url():
    assert _cfg().resume_fingerprint() != _cfg(base_url="https://api.anthropic.com").resume_fingerprint()


def test_fingerprint_diverges_on_model():
    assert _cfg().resume_fingerprint() != _cfg(model="deepseek-ai/DeepSeek-V4-Pro").resume_fingerprint()


def test_fingerprint_diverges_on_config_dir(monkeypatch):
    # The config dir decides which session store the CLI reads — a changed
    # dir must invalidate the handle even when everything else matches.
    from xyz_agent_context.settings import settings

    before = _cfg().resume_fingerprint()
    monkeypatch.setattr(settings, "claude_cli_config_path", "/somewhere/else/claude_config")
    assert _cfg().resume_fingerprint() != before


def test_fingerprint_oauth_uses_oauth_config_dir(monkeypatch):
    # Changing the KEYED dir must not move an oauth fingerprint (and vice
    # versa) — the dispatch is branch-identical to to_cli_env.
    from xyz_agent_context.settings import settings

    oauth_before = _cfg(auth_type="oauth").resume_fingerprint()
    monkeypatch.setattr(settings, "claude_cli_config_path", "/somewhere/else/claude_config")
    assert _cfg(auth_type="oauth").resume_fingerprint() == oauth_before
    monkeypatch.setattr(settings, "claude_oauth_config_path", "/somewhere/else/oauth_config")
    assert _cfg(auth_type="oauth").resume_fingerprint() != oauth_before
