"""
@file_name: test_codex_sdk_v2_init.py
@date: 2026-06-04
@description: Import-contract tests for CodexSDKv2.

Verifies:
* Protocol conformance (CodexSDKv2 satisfies AgentLoopDriver via
  structural typing).
* Method signature compatibility with v1 CodexSDK so step_3's
  driver-agnostic dispatch sees identical surfaces.
* Registry: both ``codex_cli_v2`` and ``codex_official`` names
  resolve to a CodexSDKv2 instance.
* ``_build_codex_config_overrides`` produces the right TOML-literal
  strings for the canonical inputs (MCP urls, sandbox, reasoning
  summary, permissions, writable roots).

These tests do NOT exercise the SDK itself (no subprocess, no
network) — they just lock in the static contract of v2. Live SDK
behavior is verified by ``scripts/spike_codex_official_sdk.py``
Section A/B and by Task 10's manual smoke gate.
"""
from __future__ import annotations

import inspect
from pathlib import Path

import pytest

from xyz_agent_context.agent_framework.agent_loop_driver import (
    AgentLoopDriver,
    available_agent_loop_frameworks,
    get_agent_loop_driver,
)
from xyz_agent_context.agent_framework.xyz_codex_cli_sdk import CodexSDK
from xyz_agent_context.agent_framework.xyz_codex_official_sdk import (
    CodexSDKv2,
    _build_codex_config_overrides,
)


# ---------------- Protocol conformance ----------------


def test_codex_sdk_v2_is_agent_loop_driver():
    """Structural typing check — CodexSDKv2 must conform to the
    AgentLoopDriver Protocol (defined in agent_loop_driver.py)."""
    instance = CodexSDKv2("./")
    assert isinstance(instance, AgentLoopDriver), (
        "CodexSDKv2 doesn't conform to AgentLoopDriver Protocol — "
        "check the agent_loop method signature matches the Protocol."
    )


def test_codex_sdk_v2_signature_matches_v1():
    """v1 and v2 must expose the SAME agent_loop signature so the
    dispatcher in step_3_agent_loop sees them as interchangeable."""
    v1_sig = inspect.signature(CodexSDK.agent_loop)
    v2_sig = inspect.signature(CodexSDKv2.agent_loop)
    # Same parameter names in same order. Forward-reference type
    # hints (string vs evaluated) may differ across files, so we
    # only assert on parameter names, not annotations.
    assert list(v1_sig.parameters.keys()) == list(v2_sig.parameters.keys()), (
        f"v1 params {list(v1_sig.parameters)} vs "
        f"v2 params {list(v2_sig.parameters)}"
    )


def test_codex_sdk_v2_init_takes_working_path():
    """Factory contract: ``CodexSDKv2(working_path='...')`` matches the
    factory shape ``get_agent_loop_driver`` forwards kwargs to."""
    instance = CodexSDKv2(working_path="/tmp/x")
    assert instance.working_path == "/tmp/x"


# ---------------- Registry registration ----------------


def test_v2_registered_under_canonical_name():
    """``codex_cli_v2`` is the canonical value in user_slots.agent_framework."""
    assert "codex_cli_v2" in available_agent_loop_frameworks()


def test_v2_registered_under_alias():
    """``codex_official`` is the short alias for env / CLI overrides."""
    assert "codex_official" in available_agent_loop_frameworks()


def test_v2_resolves_to_correct_class():
    driver = get_agent_loop_driver(framework="codex_cli_v2", working_path="./")
    assert isinstance(driver, CodexSDKv2)


def test_v2_alias_resolves_to_same_class():
    """Both names must produce the same driver type."""
    d1 = get_agent_loop_driver(framework="codex_cli_v2", working_path="./")
    d2 = get_agent_loop_driver(framework="codex_official", working_path="./")
    assert type(d1) is type(d2) is CodexSDKv2


def test_v1_still_registered_unaffected():
    """Adding v2 must not displace v1 — regression guard."""
    assert "codex_cli" in available_agent_loop_frameworks()
    d = get_agent_loop_driver(framework="codex_cli", working_path="./")
    assert isinstance(d, CodexSDK)


# ---------------- _build_codex_config_overrides ----------------


def test_overrides_emits_required_baseline_keys():
    """Every config must carry instructions file + sandbox + reasoning
    summary — those are the three baseline keys v1 also always emits."""
    result = _build_codex_config_overrides(
        instructions_path=Path("/tmp/agent/instructions.md"),
        mcp_server_urls={},
        permissions=None,
    )
    joined = "\n".join(result)
    assert 'model_instructions_file="/tmp/agent/instructions.md"' in joined
    assert 'sandbox_mode="danger-full-access"' in joined
    assert 'model_reasoning_summary="detailed"' in joined


def test_overrides_emits_one_mcp_entry_per_server():
    """One ``mcp_servers.<name>.url=...`` line per URL passed in."""
    result = _build_codex_config_overrides(
        instructions_path=Path("/tmp/i.md"),
        mcp_server_urls={
            "lark_module": "http://localhost:7831/mcp",
            "slack_module": "http://localhost:7832/mcp",
        },
        permissions=None,
    )
    joined = "\n".join(result)
    assert 'mcp_servers.lark_module.url="http://localhost:7831/mcp"' in joined
    assert 'mcp_servers.slack_module.url="http://localhost:7832/mcp"' in joined


def test_overrides_rewrites_sse_url_to_streamable_http():
    """MCP URL rewriter (imported from v1) must apply: ``/sse`` → ``/mcp``."""
    result = _build_codex_config_overrides(
        instructions_path=Path("/tmp/i.md"),
        mcp_server_urls={"x": "http://localhost:7801/sse"},
        permissions=None,
    )
    joined = "\n".join(result)
    assert 'mcp_servers.x.url="http://localhost:7801/mcp"' in joined


def test_overrides_quotes_glob_keys_in_permissions():
    """Permission rule keys with shell-meta chars MUST be TOML-quoted
    so codex's --config parser doesn't try to expand them as wildcards.
    This is the bug class spike Section A surfaced — guard with a test."""
    result = _build_codex_config_overrides(
        instructions_path=Path("/tmp/i.md"),
        mcp_server_urls={},
        permissions={
            "commands": {
                "brew install *": "deny",
                "sudo *": "deny",
            },
            "filesystem": {
                "/etc/**": "deny",
            },
        },
    )
    joined = "\n".join(result)
    # Each glob-bearing key must be wrapped in double quotes on the LHS.
    assert 'permissions.commands."brew install *"="deny"' in joined
    assert 'permissions.commands."sudo *"="deny"' in joined
    assert 'permissions.filesystem."/etc/**"="deny"' in joined


def test_overrides_emits_writable_roots_array():
    """When writable_roots is passed, emit the
    ``sandbox_workspace_write.writable_roots`` array entry."""
    result = _build_codex_config_overrides(
        instructions_path=Path("/tmp/i.md"),
        mcp_server_urls={},
        permissions=None,
        writable_roots=[Path("/tmp/agent"), Path("/scratch")],
    )
    joined = "\n".join(result)
    assert 'sandbox_workspace_write.writable_roots=["/tmp/agent", "/scratch"]' in joined


def test_overrides_omits_model_when_none():
    """``model=None`` means "use codex's default" — don't emit the line."""
    result = _build_codex_config_overrides(
        instructions_path=Path("/tmp/i.md"),
        mcp_server_urls={},
        permissions=None,
        model=None,
    )
    assert not any(entry.startswith("model=") for entry in result)


def test_overrides_emits_model_when_provided():
    result = _build_codex_config_overrides(
        instructions_path=Path("/tmp/i.md"),
        mcp_server_urls={},
        permissions=None,
        model="gpt-5.5",
    )
    joined = "\n".join(result)
    assert 'model="gpt-5.5"' in joined


def test_overrides_returns_tuple_not_list():
    """``CodexConfig.config_overrides`` is typed ``tuple[str, ...]``; if
    we accidentally return a list, the dataclass init would still
    accept it but type checkers complain. Lock in tuple."""
    result = _build_codex_config_overrides(
        instructions_path=Path("/tmp/i.md"),
        mcp_server_urls={},
        permissions=None,
    )
    assert isinstance(result, tuple)
    assert all(isinstance(s, str) for s in result)


def test_thread_start_accepts_kwargs_we_actually_pass():
    """SDK contract test — ``AsyncCodex.thread_start`` must keep
    accepting the kwargs we pass at runtime. The v2 ``agent_loop``
    crashed on 2026-06-08 because it passed
    ``skip_git_repo_check=True`` (a v1 CLI flag I mistakenly assumed
    the SDK exposed) — TypeError "unexpected keyword argument".

    Pin the kwarg set so an SDK rename / removal fails CI before
    hitting a user. If this test fails after an SDK upgrade:
      1. Inspect ``inspect.signature(AsyncCodex.thread_start)``
      2. Update ``xyz_codex_official_sdk.py``'s thread_start call to
         match the new shape
      3. Update this test to match what we actually pass now.
    """
    import inspect as _inspect

    from openai_codex import AsyncCodex

    sig = _inspect.signature(AsyncCodex.thread_start)
    params = set(sig.parameters.keys())

    # Kwargs the v2 agent_loop currently passes — every one of these
    # MUST be in the SDK signature or thread_start blows up at runtime.
    required = {"sandbox"}
    missing = required - params
    assert not missing, (
        f"openai_codex.AsyncCodex.thread_start lost kwarg(s) {missing}. "
        f"Update xyz_codex_official_sdk.py:agent_loop and this test. "
        f"Available kwargs: {sorted(params)}"
    )

    # Belt-and-suspenders: explicitly assert the kwarg we used to
    # pass and removed is still NOT here — if the SDK reintroduces
    # skip_git_repo_check we can re-enable it (and the CodexConfig
    # launch_args_override workaround becomes unnecessary).
    assert "skip_git_repo_check" not in params, (
        "SDK reintroduced skip_git_repo_check — consider re-enabling "
        "it at the thread_start call site for clearer intent."
    )


def test_sandbox_full_access_attribute_exists():
    """SDK contract test — the ``openai_codex.Sandbox`` enum must expose
    ``full_access``. The v2 ``agent_loop`` passes this to
    ``thread_start(sandbox=...)``. If the SDK ever renames the enum
    (the move from 0.1.0bN's ``danger_full_access`` to plain
    ``full_access`` already happened once and burned an integration
    smoke run on 2026-06-08), this test catches it before it ships.

    Note the two-layer naming convention preserved on purpose:
    * codex internal config / TOML / CLI flag:  ``danger-full-access``
    * openai_codex SDK ``Sandbox`` enum:        ``full_access``
    Both refer to the same sandbox mode."""
    from openai_codex import Sandbox

    assert hasattr(Sandbox, "full_access"), (
        "openai_codex.Sandbox.full_access missing — SDK upgrade likely "
        "renamed the enum. Update xyz_codex_official_sdk.py line 390 "
        "and this test together."
    )
    # Adjacent enum values we don't currently use but want to detect if
    # they ever disappear (would signal a much bigger SDK API rework).
    assert hasattr(Sandbox, "read_only")
    assert hasattr(Sandbox, "workspace_write")
