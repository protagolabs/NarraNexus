"""
@file_name: test_claude_sdk_resume.py
@author:
@date: 2026-07-28
@description: Claude adapter resume behavior (R2) + stale-handle same-turn
cold retry (R3), with the SDK transport stubbed out. Reworked 2026-07-28 for
the materializer split/assemble design: the adapter now drives
``split_for_argv`` (one pop) + ``assemble_argv_prompt`` (history folded only
on cold paths) instead of a private ``_assemble_system_prompt``. Contracts
under test:

* resume kwarg → ClaudeAgentOptions.resume is set AND the system prompt
  carries NO history block (system instructions still pass every round);
* no kwarg → cold start, identical to pre-resume behavior (resume=None,
  history block present);
* stale handle (zero output + CLI stderr "No conversation found") → exactly
  ONE cold retry in the same turn, with history restored and the
  response.resume_failed marker yielded (internal signal, never an
  ErrorMessage — the user must not perceive the miss);
* any other failure shape → NO retry (existing error paths unchanged);
* natural completion → graceful CLI shutdown (stdin close + bounded wait)
  BEFORE disconnect's SIGTERM teardown; cancellation skips it;
* teardown liveness (2026-07-28): a HANGING ``end_input()`` is bounded on its
  own (suppress() does nothing for a hang) and a cancellation raised DURING
  the graceful wait short-circuits to the fast teardown instead of buying the
  user the full graceful ceiling.
"""
from __future__ import annotations

import asyncio
import time

import pytest

import xyz_agent_context.agent_framework.adapters.claude.sdk as sdk_mod
from xyz_agent_context.agent_framework.adapters.claude.sdk import ClaudeAgentSDK
from xyz_agent_context.agent_framework.api_config import (
    ClaudeConfig,
    CodexConfig,
    OpenAIConfig,
    set_user_config,
)

HISTORY_MARK = "=== Chat History ==="


class ResultMessage:
    """Name matters: output_transfer dispatches on type(message).__name__."""

    def __init__(self, session_id: str = "cli_new_session"):
        self.usage = {"input_tokens": 10, "output_tokens": 5}
        self.total_cost_usd = 0.0
        self.num_turns = 1
        self.session_id = session_id
        self.stop_reason = "end_turn"


class _StubProcess:
    """Mimics the transport's subprocess handle used by the graceful path.

    ``process_hangs`` in the script simulates a CLI that never exits after
    stdin close — the case the graceful wait's bound (and its cancellation
    race) exists for.
    """

    def __init__(self, owner: "_StubClient"):
        self._owner = owner
        self.returncode = None

    async def wait(self):
        self._owner.calls.append("process_wait")
        if self._owner._script.get("process_hangs"):
            await asyncio.Event().wait()  # never resolves
        self.returncode = 0
        return 0


class _StubTransport:
    """Mimics the SDK transport surface `_graceful_cli_shutdown` reaches into.

    Script hooks:
      ``end_input_hangs``   — end_input() never returns (a stuck concurrent
                              write holding transport._write_lock).
      ``cancel_on_end_input`` — a token whose event is set from inside
                              end_input(), i.e. Stop pressed just AFTER the
                              caller's cancellation gate.
    """

    def __init__(self, owner: "_StubClient"):
        self._owner = owner
        self._process = _StubProcess(owner)

    async def end_input(self):
        self._owner.calls.append("end_input")
        token = self._owner._script.get("cancel_on_end_input")
        if token is not None:
            token.fire()
        if self._owner._script.get("end_input_hangs"):
            await asyncio.Event().wait()  # never resolves


class _StubClient:
    """Stub for ClaudeSDKClient: replays a per-instance script.

    ``scripts`` is a list of dicts consumed in construction order:
      {"messages": [...], "stderr": [...], "raise_after": Exception | None}

    Records lifecycle calls in ``calls`` (connect / query / end_input /
    process_wait / disconnect) so tests can assert TEARDOWN ORDER — the
    2026-07-25 transcript-flush regression was exactly an ordering bug
    (SIGTERM before the CLI's own clean exit).
    """

    scripts: list[dict] = []
    instances: list["_StubClient"] = []

    def __init__(self, options):
        self.options = options
        self.queried: str | None = None
        self.calls: list[str] = []
        self._transport = _StubTransport(self)
        idx = len(type(self).instances)
        self._script = type(self).scripts[idx] if idx < len(type(self).scripts) else {}
        type(self).instances.append(self)

    async def connect(self):
        self.calls.append("connect")
        # Feed the scripted CLI stderr through the real callback so the
        # adapter's stale-handle detection reads it exactly like production.
        for line in self._script.get("stderr", []):
            self.options.stderr(line)

    async def query(self, message):
        self.calls.append("query")
        self.queried = message

    def receive_response(self):
        messages = self._script.get("messages", [])
        raise_after = self._script.get("raise_after")

        async def _gen():
            for m in messages:
                yield m
            if raise_after is not None:
                raise raise_after

        return _gen()

    async def disconnect(self):
        self.calls.append("disconnect")


@pytest.fixture(autouse=True)
def _stub_transport(monkeypatch):
    _StubClient.scripts = []
    _StubClient.instances = []
    monkeypatch.setattr(sdk_mod, "ClaudeSDKClient", _StubClient)
    # Deterministic provider config for the ambient ContextVar proxy.
    set_user_config(
        claude=ClaudeConfig(api_key="k", auth_type="api_key"),
        openai=OpenAIConfig(),
        codex=CodexConfig(),
    )
    # This file covers the HANDLE-based resume path: a session id supplied by
    # upstream, which may turn out stale. The self-authored transcript
    # (2026-07-29) is a different mechanism that makes every turn a resume turn
    # and cannot go stale, so it is switched off here — otherwise the
    # "no kwarg → cold start" cases below would silently become resume cases and
    # stop testing what they name. Its own coverage lives in
    # test_claude_synthetic_transcript.py.
    from xyz_agent_context.settings import settings

    monkeypatch.setattr(settings, "claude_synthetic_transcript_enabled", False)
    yield


def _messages() -> list[dict]:
    return [
        {"role": "system", "content": "SYSTEM INSTRUCTIONS"},
        {"role": "user", "content": "old question", "_source": "chat"},
        {"role": "assistant", "content": "old answer", "_source": "chat"},
        {"role": "user", "content": "this turn input"},
    ]


async def _run(**kwargs) -> list[dict]:
    sdk = ClaudeAgentSDK(working_path="/tmp/ws")
    return [e async for e in sdk.agent_loop(_messages(), {}, **kwargs)]


# ---------------------------------------------------------------------------
# R2: resume kwarg → options.resume + history skipped
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_resume_kwarg_sets_options_resume_and_skips_history():
    _StubClient.scripts = [{"messages": [ResultMessage()]}]
    await _run(resume_session_id="cli_old_session")

    assert len(_StubClient.instances) == 1
    opts = _StubClient.instances[0].options
    assert opts.resume == "cli_old_session"
    # History lives in the CLI session file — the prompt must NOT carry it.
    assert HISTORY_MARK not in opts.system_prompt
    # The system prompt itself still passes every round.
    assert "SYSTEM INSTRUCTIONS" in opts.system_prompt
    # This turn's user input still goes through query, not the prompt.
    assert _StubClient.instances[0].queried == "this turn input"


@pytest.mark.asyncio
async def test_no_resume_kwarg_is_cold_start_with_history():
    _StubClient.scripts = [{"messages": [ResultMessage()]}]
    await _run()

    assert len(_StubClient.instances) == 1
    opts = _StubClient.instances[0].options
    assert opts.resume is None
    assert HISTORY_MARK in opts.system_prompt
    assert "old question" in opts.system_prompt
    assert "SYSTEM INSTRUCTIONS" in opts.system_prompt


@pytest.mark.asyncio
async def test_resume_run_events_flow_through_unchanged():
    _StubClient.scripts = [{"messages": [ResultMessage(session_id="cli_fresh")]}]
    events = await _run(resume_session_id="cli_old_session")

    done = [e for e in events if e.get("data", {}).get("type") == "response.done"]
    assert len(done) == 1
    assert done[0]["data"]["session_id"] == "cli_fresh"
    # No marker on a successful resume.
    assert not any(
        e.get("data", {}).get("type") == "response.resume_failed" for e in events
    )


# ---------------------------------------------------------------------------
# R3: stale handle → exactly one same-turn cold retry + marker
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_stale_handle_retries_cold_once_with_history_and_marker():
    _StubClient.scripts = [
        # Attempt 1 (resume): CLI finds no conversation → zero messages.
        {"messages": [], "stderr": ["Error: No conversation found with session ID cli_old_session"]},
        # Attempt 2 (cold retry): normal run reporting a NEW session id.
        {"messages": [ResultMessage(session_id="cli_retry_new")]},
    ]
    events = await _run(resume_session_id="cli_old_session")

    assert len(_StubClient.instances) == 2
    first, second = _StubClient.instances
    assert first.options.resume == "cli_old_session"
    assert HISTORY_MARK not in first.options.system_prompt
    # Cold retry: resume cleared, history restored, same user input re-sent.
    assert second.options.resume is None
    assert HISTORY_MARK in second.options.system_prompt
    assert "old question" in second.options.system_prompt
    assert second.queried == "this turn input"

    # Marker precedes the retry's events; the zero-output error is swallowed.
    types = [e.get("data", {}).get("type") for e in events]
    assert "response.resume_failed" in types
    assert types.index("response.resume_failed") < types.index("response.done")
    assert "response.error" not in types  # user never perceives the miss

    # The retry's fresh handle rides response.done for step_4's upsert.
    done = [e for e in events if e.get("data", {}).get("type") == "response.done"]
    assert done[0]["data"]["session_id"] == "cli_retry_new"


@pytest.mark.asyncio
async def test_zero_output_without_stale_phrase_does_not_retry():
    _StubClient.scripts = [
        {"messages": [], "stderr": ["Error: something else entirely"]},
    ]
    events = await _run(resume_session_id="cli_old_session")

    assert len(_StubClient.instances) == 1  # no retry
    types = [e.get("data", {}).get("type") for e in events]
    assert "response.resume_failed" not in types
    # The zero-output error surfaces exactly as before.
    assert types == ["response.error"]
    assert events[0]["data"]["error_type"] == "no_output"


@pytest.mark.asyncio
async def test_zero_output_on_cold_start_never_retries():
    # The stale phrase can't appear on a cold run, but even if stderr
    # contained it, no resume was requested → single run, no marker.
    _StubClient.scripts = [
        {"messages": [], "stderr": ["No conversation found (bogus echo)"]},
    ]
    events = await _run()

    assert len(_StubClient.instances) == 1
    types = [e.get("data", {}).get("type") for e in events]
    assert "response.resume_failed" not in types
    assert "response.error" in types


@pytest.mark.asyncio
async def test_stale_phrase_only_in_exception_with_empty_stderr_triggers_retry():
    # 2026-07-25 live gap: the stale resume died as the SDK's ProcessError
    # (exit 1) with EMPTY captured stderr — the old RuntimeError+stderr-only
    # predicate never fired. The phrase in the exception text alone must now
    # trigger the (exactly-once) cold retry.
    _StubClient.scripts = [
        {
            "messages": [],
            "stderr": [],  # stderr pump lost the race — nothing captured
            "raise_after": Exception(
                "No conversation found with session ID: cli_old_session"
            ),
        },
        {"messages": [ResultMessage(session_id="cli_retry_new2")]},
    ]
    events = await _run(resume_session_id="cli_old_session")

    assert len(_StubClient.instances) == 2
    types = [e.get("data", {}).get("type") for e in events]
    assert "response.resume_failed" in types
    second = _StubClient.instances[1]
    assert second.options.resume is None
    assert HISTORY_MARK in second.options.system_prompt


@pytest.mark.asyncio
async def test_exception_without_phrase_reraises_no_retry():
    _StubClient.scripts = [
        {"messages": [], "stderr": [], "raise_after": Exception("boom, unrelated")},
        {"messages": [ResultMessage()]},  # must NOT be consumed
    ]
    with pytest.raises(Exception, match="boom, unrelated"):
        await _run(resume_session_id="cli_old_session")
    assert len(_StubClient.instances) == 1


def test_failure_predicate_checks_all_evidence_channels():
    from xyz_agent_context.agent_framework.adapters.claude.sdk import (
        _failure_indicates_stale_resume,
    )

    phrase = "No conversation found with session ID: x"
    # Channel 1: exception text only.
    assert _failure_indicates_stale_resume(Exception(phrase), [])
    # Channel 2: captured stderr only.
    assert _failure_indicates_stale_resume(Exception("exit code 1"), [phrase])

    # Channel 3: the exception's stderr attribute (ProcessError shape) only.
    class _ProcErr(Exception):
        stderr = phrase

    assert _failure_indicates_stale_resume(_ProcErr("exit code 1"), [])
    # Nowhere → no retry.
    assert not _failure_indicates_stale_resume(Exception("exit code 1"), ["other"])


# ---------------------------------------------------------------------------
# 2026-07-25 transcript-flush regression: teardown ORDER on normal completion
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_normal_completion_graceful_shutdown_before_teardown():
    # The CLI must get its own clean exit (stdin close → process exits)
    # BEFORE disconnect()'s SIGTERM teardown — that clean exit is what
    # flushes the session transcript `--resume` replays next turn.
    _StubClient.scripts = [{"messages": [ResultMessage()]}]
    await _run()

    (client,) = _StubClient.instances
    assert client.calls == ["connect", "query", "end_input", "process_wait", "disconnect"]
    # After the graceful wait the process is already gone — the transport's
    # close() (inside disconnect) then skips SIGTERM entirely.
    assert client._transport._process.returncode == 0


@pytest.mark.asyncio
async def test_resume_run_also_gets_graceful_shutdown():
    _StubClient.scripts = [{"messages": [ResultMessage()]}]
    await _run(resume_session_id="cli_old_session")

    (client,) = _StubClient.instances
    assert client.calls == ["connect", "query", "end_input", "process_wait", "disconnect"]


class _CancelledToken:
    is_cancelled = True

    async def await_cancelled(self):
        return None


@pytest.mark.asyncio
async def test_cancelled_run_skips_graceful_shutdown_but_still_tears_down():
    # Cancellation keeps the fast synchronous teardown: NO graceful wait
    # (the user pressed stop — do not linger), straight to disconnect.
    _StubClient.scripts = [{"messages": [ResultMessage()]}]
    sdk = ClaudeAgentSDK(working_path="/tmp/ws")
    _ = [
        e
        async for e in sdk.agent_loop(_messages(), {}, cancellation=_CancelledToken())
    ]

    (client,) = _StubClient.instances
    assert "end_input" not in client.calls
    assert "process_wait" not in client.calls
    assert client.calls[-1] == "disconnect"


# ---------------------------------------------------------------------------
# 2026-07-28 teardown liveness: bounded end_input + cancel-during-graceful
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_hanging_end_input_is_bounded_and_turn_still_completes(monkeypatch):
    # The vendored SDK's end_input() takes transport._write_lock; a stuck
    # concurrent write makes it hang forever, and `suppress(Exception)` does
    # NOTHING for a hang. It must have its own bound and then fall through to
    # the SIGTERM/SIGKILL teardown path.
    monkeypatch.setattr(sdk_mod, "_GRACEFUL_END_INPUT_SECONDS", 0.05)
    monkeypatch.setattr(sdk_mod, "_GRACEFUL_CLI_EXIT_SECONDS", 0.05)
    _StubClient.scripts = [
        {
            "messages": [ResultMessage()],
            "end_input_hangs": True,
            "process_hangs": True,  # nothing rescues us but the bound
        }
    ]
    started = time.monotonic()
    events = await _run()
    elapsed = time.monotonic() - started

    # The turn completed normally — the hang never reached the user.
    types = [e.get("data", {}).get("type") for e in events]
    assert "response.done" in types
    # Bounded by the two small ceilings, nowhere near the 10s default.
    assert elapsed < 2.0
    (client,) = _StubClient.instances
    assert client.calls[-1] == "disconnect"  # fell through to the kill path


class _LateCancelToken:
    """Not cancelled at the caller's gate; fires DURING graceful shutdown."""

    def __init__(self):
        self._event = asyncio.Event()

    @property
    def is_cancelled(self) -> bool:
        return self._event.is_set()

    def fire(self) -> None:
        self._event.set()

    async def await_cancelled(self):
        await self._event.wait()


@pytest.mark.asyncio
async def test_cancel_during_graceful_wait_short_circuits_to_teardown():
    # Stop pressed a millisecond AFTER the caller's one-shot gate: the
    # graceful wait must lose the race to the token instead of holding the
    # user for the full _GRACEFUL_CLI_EXIT_SECONDS.
    token = _LateCancelToken()
    _StubClient.scripts = [
        {
            "messages": [ResultMessage()],
            "cancel_on_end_input": token,   # fires once we're already inside
            "process_hangs": True,          # CLI would never exit on its own
        }
    ]
    sdk = ClaudeAgentSDK(working_path="/tmp/ws")
    started = time.monotonic()
    _ = [e async for e in sdk.agent_loop(_messages(), {}, cancellation=token)]
    elapsed = time.monotonic() - started

    # Fast teardown — NOT the 10s graceful ceiling.
    assert elapsed < 2.0
    assert sdk_mod._GRACEFUL_CLI_EXIT_SECONDS == 10.0  # ceiling itself unchanged
    (client,) = _StubClient.instances
    # We did enter the graceful path (gate passed), then bailed out on cancel.
    assert "end_input" in client.calls
    assert client.calls[-1] == "disconnect"
    assert client._transport._process.returncode is None  # no clean exit waited


@pytest.mark.asyncio
async def test_normal_completion_still_waits_the_full_graceful_exit():
    # Guard against "fix cancellation, regress the transcript flush": with NO
    # cancellation the graceful wait must still await the CLI's own exit.
    _StubClient.scripts = [{"messages": [ResultMessage()]}]
    await _run()
    (client,) = _StubClient.instances
    assert client.calls == ["connect", "query", "end_input", "process_wait", "disconnect"]
    assert client._transport._process.returncode == 0


@pytest.mark.asyncio
async def test_failure_after_content_reraises_without_retry():
    # Content already reached the user → NOT "failed-before-any-content";
    # the crash must flow through the existing error path, no retry.
    _StubClient.scripts = [
        {
            "messages": [ResultMessage()],
            "stderr": ["No conversation found"],
            "raise_after": RuntimeError("boom mid-stream"),
        },
        {"messages": [ResultMessage()]},  # must NOT be consumed
    ]
    with pytest.raises(RuntimeError, match="boom mid-stream"):
        await _run(resume_session_id="cli_old_session")
    assert len(_StubClient.instances) == 1
