"""
@file_name: test_claude_task_list_tools.py
@date: 2026-09-09
@description: The Claude Code CLI's task-LIST tools (TaskCreate / TaskGet /
TaskList / TaskUpdate) are reachable from the model but wired to nothing on
the platform: the list lives only inside the CLI process, so whatever the
model queues there is orphaned the moment the run ends (GitHub #74). The
adapter must switch the feature off on EVERY run (any provider, any auth) —
CLAUDE_CODE_ENABLE_TASKS=false at the source plus the four names on
disallowed_tools — and tell the model that work outliving the run goes
through the Job module, while in-run background commands keep working.

Which names belong to the family is a fact about the CLI binary, not about
this file. The hand-written set below is checked against the bundled binary
whenever it is present (``_cli_binary``): the four names must be there, and
the two look-alikes that are NOT task-list tools — TaskOutput (the
BashOutput/AgentOutput tool) and TaskStop (KillShell) — must be recognised
as such. To re-verify by hand:

    B=$(python -c "import claude_agent_sdk,os;print(os.path.dirname(claude_agent_sdk.__file__))")/_bundled/claude
    grep -aoE 'isEnabled\(\)\{return T4\(\)\}' "$B" | wc -l   # the T4 (ENABLE_TASKS) gate
    grep -ao 'aliases:\["AgentOutputTool","BashOutputTool"\]' "$B"  # TaskOutput
    grep -ao 'aliases:\["KillShell"\]' "$B"                         # TaskStop
    grep -aoE '.{60}CLAUDE_CODE_ENABLE_TASKS.{80}' "$B" | head -1   # T4 reads the env

Each behavioural test goes red when the env injection / the disallow list /
the prompt notice is removed. The WebSearch guard is asserted alongside so
the family disallow cannot accidentally replace it (merge, never replace).
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest

import narranexus_plugins.frameworks_claude_code.sdk as sdk_mod
from narranexus_plugins.frameworks_claude_code.prompts import task_list_tools_notice
from narranexus_plugins.frameworks_claude_code.sdk import (
    CLI_ENABLE_TASKS_ENV,
    TASK_LIST_TOOLS,
    ClaudeAgentSDK,
)
from narranexus.platform.agent_framework.api_config import (
    ClaudeConfig,
    CodexConfig,
    OpenAIConfig,
    set_user_config,
)

from tests.agent_framework.test_claude_sdk_resume import ResultMessage, _StubClient

_TASK_LIST_TOOLS = {"TaskCreate", "TaskGet", "TaskList", "TaskUpdate"}
# Same prefix, different feature: in-run background command tools.
_IN_RUN_BACKGROUND_TOOLS = {"TaskOutput", "TaskStop"}


def _cli_binary() -> Path | None:
    try:
        import claude_agent_sdk
    except ImportError:
        return None
    path = Path(os.path.dirname(claude_agent_sdk.__file__)) / "_bundled" / "claude"
    return path if path.is_file() else None


@pytest.fixture(autouse=True)
def _stub_transport(monkeypatch):
    _StubClient.scripts = [{"messages": [ResultMessage()]}]
    _StubClient.instances = []
    monkeypatch.setattr(sdk_mod, "ClaudeSDKClient", _StubClient)
    from narranexus.platform.settings import settings

    monkeypatch.setattr(settings, "claude_synthetic_transcript_enabled", False)
    yield


def _configure(*, supports_server_tools: bool) -> None:
    set_user_config(
        claude=ClaudeConfig(
            api_key="k",
            auth_type="api_key",
            supports_anthropic_server_tools=supports_server_tools,
        ),
        openai=OpenAIConfig(),
        codex=CodexConfig(),
    )


def _messages() -> list[dict]:
    return [
        {"role": "system", "content": "SYSTEM INSTRUCTIONS"},
        {"role": "user", "content": "this turn input"},
    ]


async def _run_and_capture_options(**kwargs):
    sdk = ClaudeAgentSDK(working_path="/tmp/ws-task-list-tools")
    _ = [e async for e in sdk.agent_loop(_messages(), {}, **kwargs)]
    (client,) = _StubClient.instances
    return client.options


# ── the family itself ─────────────────────────────────────────────────────


def test_constant_is_exactly_the_task_list_family():
    assert set(TASK_LIST_TOOLS) == _TASK_LIST_TOOLS
    assert not (set(TASK_LIST_TOOLS) & _IN_RUN_BACKGROUND_TOOLS)
    assert "Task" not in TASK_LIST_TOOLS  # sub-agent launch stays allowed
    assert CLI_ENABLE_TASKS_ENV == "CLAUDE_CODE_ENABLE_TASKS"


def test_family_matches_the_bundled_cli_binary():
    """The names and the env gate are facts about the binary; check them
    there instead of against a second hand-written copy."""
    binary = _cli_binary()
    if binary is None:
        pytest.skip("claude-agent-sdk bundled binary not installed")
    blob = binary.read_bytes()
    for name in TASK_LIST_TOOLS:
        assert f'"{name}"'.encode() in blob, name
    assert CLI_ENABLE_TASKS_ENV.encode() in blob
    # The look-alikes are other tools (BashOutput / KillShell) — proof they
    # do not belong in the set.
    assert b'aliases:["AgentOutputTool","BashOutputTool"]' in blob
    assert b'aliases:["KillShell"]' in blob


# ── behaviour on the built options ────────────────────────────────────────


@pytest.mark.asyncio
async def test_feature_is_switched_off_in_the_cli_env():
    _configure(supports_server_tools=False)
    options = await _run_and_capture_options()
    assert options.env[CLI_ENABLE_TASKS_ENV] == "false"


@pytest.mark.asyncio
async def test_skill_env_cannot_switch_the_feature_back_on():
    _configure(supports_server_tools=False)
    options = await _run_and_capture_options(
        extra_env={CLI_ENABLE_TASKS_ENV: "true", "TAVILY_API_KEY": "t"}
    )
    assert options.env[CLI_ENABLE_TASKS_ENV] == "false"
    assert options.env["TAVILY_API_KEY"] == "t"  # other skill env untouched


@pytest.mark.asyncio
async def test_task_list_tools_disallowed_and_websearch_kept_without_server_tools():
    _configure(supports_server_tools=False)
    options = await _run_and_capture_options()

    disallowed = set(options.disallowed_tools)
    assert _TASK_LIST_TOOLS <= disallowed
    assert not (_IN_RUN_BACKGROUND_TOOLS & disallowed)
    # The pre-existing guard survives the family disallow (merge, not replace).
    assert "WebSearch" in disallowed
    assert len(options.disallowed_tools) == len(disallowed), "no duplicates"


@pytest.mark.asyncio
async def test_task_list_tools_disallowed_even_when_websearch_is_allowed():
    """Positive case for the other guard: an official-Anthropic provider keeps
    WebSearch, yet the task-list family is still gone — provider-independent."""
    _configure(supports_server_tools=True)
    options = await _run_and_capture_options()

    disallowed = set(options.disallowed_tools)
    assert _TASK_LIST_TOOLS <= disallowed
    assert "WebSearch" not in disallowed
    assert not (_IN_RUN_BACKGROUND_TOOLS & disallowed)


@pytest.mark.asyncio
async def test_upstream_disallow_list_is_merged_not_replaced():
    _configure(supports_server_tools=False)
    options = await _run_and_capture_options(
        disallowed_tools=["mcp__lark__lark_cli", "TaskList"]
    )

    disallowed = options.disallowed_tools
    assert "mcp__lark__lark_cli" in disallowed
    assert _TASK_LIST_TOOLS <= set(disallowed)
    assert "WebSearch" in disallowed
    assert disallowed.count("TaskList") == 1


# ── the prompt notice ─────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_system_prompt_points_cross_run_work_at_the_job_module():
    _configure(supports_server_tools=False)
    options = await _run_and_capture_options()

    prompt = options.system_prompt
    assert prompt.startswith("SYSTEM INSTRUCTIONS")
    notice = task_list_tools_notice(TASK_LIST_TOOLS)
    assert notice and notice in prompt
    for tool in _TASK_LIST_TOOLS:
        assert tool in notice
    for tool in _IN_RUN_BACKGROUND_TOOLS:
        assert tool in notice  # named as STILL WORKING, never as disabled
    assert "still work" in notice
    assert "Job module" in notice
    assert "create_job" in notice
    # Exactly once: the notice rides the BASE prompt, never the history.
    assert prompt.count(notice) == 1


def test_notice_is_empty_for_an_empty_tool_list():
    assert task_list_tools_notice(()) == ""
