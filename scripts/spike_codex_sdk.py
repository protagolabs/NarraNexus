"""
@file_name: spike_codex_sdk.py
@author: NarraNexus
@date: 2026-06-01
@description: Codex SDK migration capability spike.

Targets the community-published ``openai-codex-sdk`` 0.1.11
(import name ``openai_codex_sdk``) verified installed in the
NarraNexus dev venv on 2026-06-01. Its exports include:

  Codex, Thread, StreamedTurn, Turn
  ThreadOptions, TurnOptions
  SandboxMode, ApprovalMode, ModelReasoningEffort
  AbortController, AbortSignal, AbortError
  TextInput, LocalImageInput, UserInput
  ItemStartedEvent, ItemCompletedEvent, ItemUpdatedEvent
  ThreadStartedEvent, ThreadErrorEvent, ThreadEvent
  TurnStartedEvent, TurnCompletedEvent, TurnFailedEvent
  AgentMessageItem, ReasoningItem, CommandExecutionItem,
  McpToolCallItem, FileChangeItem, WebSearchItem, ErrorItem,
  TodoItem, TodoListItem
  login_with_auth_json, login_with_device_code

This spike answers three questions before we commit to migrating
``xyz_codex_cli_sdk.py``:

  0. SIGNATURE DISCOVERY — what do ``Codex.__init__``,
     ``codex.start_thread``, ``thread.run_streamed``, and
     ``ThreadOptions`` actually accept? We don't know yet, and
     guessing is how the migration breaks. The preamble uses
     ``inspect.signature`` so we get authoritative answers.

  A. MCP wiring — can ``ThreadOptions`` (or some equivalent) accept
     ``mcp_servers`` directly so we drop our hand-written
     ``$CODEX_HOME/config.toml``? Test A runs the agent with one
     MCP server passed via SDK config and watches for a
     ``McpToolCallItem`` in the event stream.

  B. Cancellation — does ``AbortController().abort()`` (the JS-style
     cancel surfaced in this SDK) release the streamed-turn awaiter
     within a few seconds? Test B starts a long counting prompt,
     aborts after 3s, measures the unblock latency.

Prerequisites
-------------
* ``uv pip install openai-codex-sdk`` (already done — Audit OK).
* ``codex login`` ran previously (so ``~/.codex/auth.json`` exists).
* HTTPS_PROXY / HTTP_PROXY exported (CN/HK network).
* Test A only: ``bash run.sh`` running so lark_module MCP is up at
  http://localhost:7831/mcp. Otherwise the agent has nothing to call
  and Test A is inconclusive.

Usage
-----
    python scripts/spike_codex_sdk.py

Paste the output back. Output shape:
  Section 0 → signatures (authoritative API contract)
  Section A → MCP transcript (PASS / FAIL / ERROR)
  Section B → cancellation timing (PASS / FAIL / ERROR)
"""
from __future__ import annotations

import asyncio
import inspect
import os
import shutil
import sys
import tempfile
import time
from pathlib import Path


# Tunable probe parameters — adjust if your local NarraNexus is
# bound to different ports / a different model.
MCP_PROBE_NAME = "lark_module"
MCP_PROBE_URL = "http://localhost:7831/mcp"
PROBE_MODEL = "gpt-5.4-mini"  # ChatGPT-OAuth tier (verified 2026-06-01)
TEST_A_TIMEOUT_S = 60.0
TEST_B_RUN_BEFORE_CANCEL_S = 3.0
TEST_B_MAX_UNBLOCK_S = 5.0


def _stage_auth(codex_home: Path) -> bool:
    src = Path.home() / ".codex" / "auth.json"
    if not src.exists():
        return False
    shutil.copy(src, codex_home / "auth.json")
    return True


# ---------------- Section 0: API discovery --------------------------


def _print_signatures() -> object | None:
    """Import the SDK and print the signatures of the symbols Test
    A and B want to call. If any expected symbol is missing, return
    None so main() short-circuits with a useful diagnostic."""
    print("=" * 64)
    print("Section 0: SDK signature discovery")
    print("=" * 64)
    try:
        import openai_codex_sdk as oc  # type: ignore[import-not-found]
    except ImportError:
        print("  openai_codex_sdk: NOT importable")
        print("  Install: uv pip install openai-codex-sdk")
        return None

    print(f"  package version: {getattr(oc, '__version__', '(no __version__)')}")
    print()

    def _sig(target: object, label: str) -> None:
        try:
            sig = inspect.signature(target)
        except (TypeError, ValueError) as e:
            print(f"  {label}: <not introspectable: {e}>")
            return
        print(f"  {label}{sig}")

    # Constructors / factories the migration path will lean on.
    candidates = [
        ("Codex.__init__", getattr(oc.Codex, "__init__", None)),
        ("Codex.start_thread", getattr(oc.Codex, "start_thread", None)),
        ("Thread.run", getattr(oc.Thread, "run", None)),
        ("Thread.run_streamed", getattr(oc.Thread, "run_streamed", None)),
        ("ThreadOptions", oc.ThreadOptions),
        ("TurnOptions", oc.TurnOptions),
        ("AbortController.__init__", getattr(oc.AbortController, "__init__", None)),
        ("AbortController.abort", getattr(oc.AbortController, "abort", None)),
    ]
    for label, target in candidates:
        if target is None:
            print(f"  {label}: <not exported>")
        else:
            _sig(target, label)

    # Enum / option members — these tell us valid values for sandbox /
    # approval / reasoning_effort without having to read the source.
    print()
    for enum_name in ("SandboxMode", "ApprovalMode", "ModelReasoningEffort"):
        enum_cls = getattr(oc, enum_name, None)
        if enum_cls is None:
            print(f"  {enum_name}: <not exported>")
            continue
        try:
            members = [m.name for m in enum_cls]  # type: ignore[attr-defined]
        except TypeError:
            members = [n for n in dir(enum_cls) if not n.startswith("_")]
        print(f"  {enum_name}: {members}")

    print()
    return oc


# ---------------- Section A: MCP wiring -----------------------------


async def test_a_mcp_wiring(oc: object) -> bool:
    """Pass mcp_servers to the SDK and ask the agent something that
    must invoke an MCP tool. PASS = at least one McpToolCallItem
    observed within TEST_A_TIMEOUT_S.

    Several wiring strategies are tried in order, since the README
    is sparse on which one is canonical:
      Strategy 1: ``ThreadOptions(mcp_servers={...})``
      Strategy 2: ``Codex(mcp_servers={...})``
      Strategy 3: raw config dict ``Codex(config={"mcp_servers": ...})``

    The first one that doesn't raise TypeError wins. We log which
    one was used so the migration knows which call site to copy.
    """
    print("=" * 64)
    print("Section A: MCP wiring via SDK")
    print("=" * 64)

    with tempfile.TemporaryDirectory(prefix="spike_codex_a_") as home_str:
        codex_home = Path(home_str)
        instructions = codex_home / "instructions.md"
        instructions.write_text(
            "You are a probe. You have MCP tools available from the "
            f"'{MCP_PROBE_NAME}' server. When asked what MCP tools "
            "you have, CALL one of them (any will do) to confirm. "
            "Do NOT use Bash; do NOT speculate from memory.\n"
        )
        if not _stage_auth(codex_home):
            print("  WARN: ~/.codex/auth.json missing — Test A will likely fail auth")

        # Inject CODEX_HOME via environment. Several SDKs respect parent
        # env; this is the cheapest path and works regardless of
        # whether ``Codex()`` exposes its own env knob.
        os.environ["CODEX_HOME"] = str(codex_home)

        mcp_cfg = {MCP_PROBE_NAME: {"url": MCP_PROBE_URL}}
        print(f"  CODEX_HOME = {codex_home}")
        print(f"  mcp_servers config = {mcp_cfg}")
        print()

        # Strategy probe loop — print which one we pick.
        codex_inst = None
        thread = None
        used_strategy = None
        for label, build in [
            ("ThreadOptions(mcp_servers=...)", lambda: (
                oc.Codex(),  # type: ignore[attr-defined]
                {"options_mcp": True},
            )),
            ("Codex(mcp_servers=...)", lambda: (
                oc.Codex(mcp_servers=mcp_cfg),  # type: ignore[attr-defined,call-arg]
                {"client_mcp": True},
            )),
            ("Codex(config={'mcp_servers': ...})", lambda: (
                oc.Codex(config={"mcp_servers": mcp_cfg}),  # type: ignore[attr-defined,call-arg]
                {"config_mcp": True},
            )),
        ]:
            try:
                codex_inst, hint = build()
                used_strategy = label
                print(f"  STRATEGY OK: {label}")
                break
            except TypeError as e:
                print(f"  strategy rejected: {label} → {e}")
            except Exception as e:  # noqa: BLE001
                print(f"  strategy errored: {label} → {type(e).__name__}: {e}")

        if codex_inst is None:
            print("  RESULT: ERROR — no Codex() constructor variant accepted")
            return False

        # Now try start_thread, preferring ThreadOptions with MCP if
        # strategy 1 was selected, otherwise plain start_thread.
        try:
            if used_strategy and "ThreadOptions" in used_strategy:
                thread_options = oc.ThreadOptions(  # type: ignore[attr-defined,call-arg]
                    working_directory=str(codex_home),
                    skip_git_repo_check=True,
                    sandbox_mode=getattr(
                        oc.SandboxMode, "workspace_write",  # type: ignore[attr-defined]
                        None,
                    ),
                    mcp_servers=mcp_cfg,
                )
                thread = codex_inst.start_thread(thread_options)
            else:
                # Try kwargs-first form (most SDKs accept kwargs at start_thread).
                thread = codex_inst.start_thread(
                    working_directory=str(codex_home),
                    skip_git_repo_check=True,
                )
        except TypeError as e:
            print(f"  start_thread signature mismatch: {e}")
            print(f"  → check Section 0 for the real signature, then re-run")
            return False
        except Exception as e:  # noqa: BLE001
            print(f"  start_thread errored: {type(e).__name__}: {e}")
            return False

        # Run a prompt that should provoke an MCP tool call.
        try:
            streamed = thread.run_streamed(
                oc.TextInput("What MCP tools do you have? "  # type: ignore[attr-defined]
                            "Call one to confirm.")
            )
        except TypeError:
            # Some SDKs accept a raw string directly.
            try:
                streamed = thread.run_streamed(
                    "What MCP tools do you have? Call one to confirm."
                )
            except Exception as e:  # noqa: BLE001
                print(f"  run_streamed call failed: {type(e).__name__}: {e}")
                return False
        except Exception as e:  # noqa: BLE001
            print(f"  run_streamed errored: {type(e).__name__}: {e}")
            return False

        # Iterate events. The exact iteration protocol differs across
        # SDKs (could be async generator on .events, on the StreamedTurn
        # itself, or sync). Try the most common shapes.
        async def _aiter(obj: object):
            if hasattr(obj, "events"):
                async for ev in obj.events:  # type: ignore[attr-defined]
                    yield ev
            elif hasattr(obj, "__aiter__"):
                async for ev in obj:  # type: ignore[misc]
                    yield ev
            else:
                raise RuntimeError(f"Cannot iterate {type(obj).__name__}")

        mcp_seen = False
        t0 = time.time()
        try:
            async for event in _aiter(streamed):
                dt = time.time() - t0
                cls = type(event).__name__
                item = getattr(event, "item", None)
                item_cls = type(item).__name__ if item is not None else "—"
                print(f"  [{dt:5.1f}s] {cls:24s} item={item_cls}")
                if "McpToolCall" in item_cls:
                    mcp_seen = True
                if dt > TEST_A_TIMEOUT_S:
                    print(f"  [TIMEOUT after {TEST_A_TIMEOUT_S:.0f}s]")
                    break
        except Exception as e:  # noqa: BLE001
            print(f"  event iteration errored: {type(e).__name__}: {e}")
            return False

        print()
        if mcp_seen:
            print(f"  RESULT: PASS — McpToolCallItem seen via {used_strategy}")
            return True
        else:
            print("  RESULT: FAIL/AMBIGUOUS — no McpToolCallItem")
            print("    either agent declined to call a tool,")
            print("    or this strategy didn't actually wire MCP through.")
            return False


# ---------------- Section B: cancellation ---------------------------


async def test_b_cancellation(oc: object) -> bool:
    """Start a long counting turn, ``AbortController.abort()`` it
    after 3s, measure how long until the awaiter releases. PASS = under
    TEST_B_MAX_UNBLOCK_S seconds. Tries SDK-native AbortController
    first, falls back to ``task.cancel()`` if abort isn't honored."""
    print("=" * 64)
    print("Section B: cancellation")
    print("=" * 64)

    with tempfile.TemporaryDirectory(prefix="spike_codex_b_") as home_str:
        codex_home = Path(home_str)
        _stage_auth(codex_home)
        os.environ["CODEX_HOME"] = str(codex_home)

        try:
            codex_inst = oc.Codex()  # type: ignore[attr-defined]
            thread = codex_inst.start_thread(
                working_directory=str(codex_home),
                skip_git_repo_check=True,
            )
        except Exception as e:  # noqa: BLE001
            print(f"  setup failed: {type(e).__name__}: {e}")
            return False

        # Build an AbortController if the SDK accepts a signal.
        controller = oc.AbortController()  # type: ignore[attr-defined]
        print(f"  AbortController created: {type(controller).__name__}")

        # Try the JS-style ``signal=`` first.
        used_path = "task.cancel"
        try:
            streamed = thread.run_streamed(
                oc.TextInput(  # type: ignore[attr-defined]
                    "Count slowly from 1 to 50. Use a full sentence per "
                    "number with explanation. Do not stop early."
                ),
                signal=controller.signal,
            )
            used_path = "AbortController.abort"
            print(f"  using {used_path} (signal accepted by run_streamed)")
        except TypeError:
            # SDK doesn't accept signal kwarg — fall back to plain task cancel.
            print(f"  signal kwarg rejected; falling back to task.cancel()")
            try:
                streamed = thread.run_streamed(
                    oc.TextInput(  # type: ignore[attr-defined]
                        "Count slowly from 1 to 50. Use a full sentence per "
                        "number with explanation. Do not stop early."
                    )
                )
            except TypeError:
                streamed = thread.run_streamed(
                    "Count slowly from 1 to 50. Use a full sentence per "
                    "number with explanation. Do not stop early."
                )

        event_count = 0

        async def _aiter(obj: object):
            if hasattr(obj, "events"):
                async for ev in obj.events:  # type: ignore[attr-defined]
                    yield ev
            else:
                async for ev in obj:  # type: ignore[misc]
                    yield ev

        async def consume() -> None:
            nonlocal event_count
            try:
                async for _ in _aiter(streamed):
                    event_count += 1
            except asyncio.CancelledError:
                print(f"  consume() caught CancelledError after {event_count} events")
                raise
            except Exception as e:  # noqa: BLE001  AbortError etc.
                print(f"  consume() caught {type(e).__name__} after {event_count} events: {e}")
                raise

        task = asyncio.create_task(consume())
        await asyncio.sleep(TEST_B_RUN_BEFORE_CANCEL_S)

        cancel_at = time.time()
        if used_path == "AbortController.abort":
            print(f"  calling controller.abort() after {TEST_B_RUN_BEFORE_CANCEL_S:.1f}s...")
            controller.abort()
        else:
            print(f"  calling task.cancel() after {TEST_B_RUN_BEFORE_CANCEL_S:.1f}s...")
            task.cancel()

        try:
            await asyncio.wait_for(task, timeout=TEST_B_MAX_UNBLOCK_S + 5)
        except (asyncio.CancelledError, asyncio.TimeoutError):
            pass
        except Exception as e:  # noqa: BLE001
            print(f"  await task raised {type(e).__name__}: {e}")
        dt = time.time() - cancel_at
        print(f"  awaiter released {dt:.2f}s after cancel")
        print()
        if dt < TEST_B_MAX_UNBLOCK_S:
            print(f"  RESULT: PASS — released in under {TEST_B_MAX_UNBLOCK_S:.0f}s "
                  f"via {used_path}")
            return True
        else:
            print(f"  RESULT: FAIL — took {dt:.1f}s (threshold {TEST_B_MAX_UNBLOCK_S:.0f}s)")
            return False


# ---------------- main ----------------------------------------------


async def main() -> None:
    print(f"  Python:       {sys.version.split()[0]}")
    print(f"  codex CLI:    {shutil.which('codex') or '(not on PATH)'}")
    auth = Path.home() / ".codex" / "auth.json"
    print(f"  auth.json:    {'present' if auth.exists() else 'MISSING — run `codex login`'}")
    print(f"  HTTPS_PROXY:  {os.environ.get('HTTPS_PROXY') or '(unset)'}")
    print()

    oc = _print_signatures()
    if oc is None:
        return
    print()

    a = await test_a_mcp_wiring(oc)
    print()
    b = await test_b_cancellation(oc)
    print()
    print("=" * 64)
    print(f"Summary: Section A {'PASS' if a else 'FAIL'} | "
          f"Section B {'PASS' if b else 'FAIL'}")
    print("=" * 64)


if __name__ == "__main__":
    asyncio.run(main())
