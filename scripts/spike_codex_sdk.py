"""
@file_name: spike_codex_sdk.py
@author: NarraNexus
@date: 2026-06-01
@description: Codex SDK migration capability spike.

Validates the two unknowns blocking the ``xyz_codex_cli_sdk`` rewrite
onto the official ``openai-codex-sdk`` Python package:

  A. Does ``Codex(config={"mcp_servers": {...}})`` actually serialize
     into ``--config mcp_servers.<name>.url=...`` flags that codex
     CLI honors, so we can drop our hand-written
     ``$CODEX_HOME/config.toml``?
  B. Does ``asyncio.CancelledError`` interrupt ``run_streamed`` within
     a few seconds, so the existing race-with-cancel pattern in
     ``xyz_codex_cli_sdk.agent_loop`` can be replaced by plain
     ``task.cancel()``?

This is intentionally standalone — no NarraNexus imports, no DB, no
modules. It just exercises the SDK surface so we know whether to
proceed with the migration or design around a limitation.

Prerequisites
-------------
* ``pip install openai-codex-sdk`` inside the active venv.
* ``codex login`` done previously (so ~/.codex/auth.json exists).
* HTTPS_PROXY / HTTP_PROXY exported in the shell running this
  script — otherwise codex can't reach OpenAI from CN/HK and Test A
  will look like an SDK problem when it's actually a network one.
* For Test A only: ``bash run.sh`` running, so the lark_module MCP
  server at http://localhost:7831/mcp is reachable. (Other ports work
  too — adjust ``MCP_PROBE_URL`` below.)

Usage
-----
    python scripts/spike_codex_sdk.py

Then paste the printed output back so we can decide:
  * Both PASS  → proceed with the SDK migration as planned.
  * A fails    → MCP must stay on hand-written config.toml; SDK only
                 buys us subprocess management, not config injection.
  * B fails    → cancellation needs a wrapper; keep race-with-cancel
                 in ``xyz_codex_cli_sdk`` or build an equivalent
                 around the SDK task.
"""
from __future__ import annotations

import asyncio
import os
import shutil
import sys
import tempfile
import time
from pathlib import Path


# Where to point Test A's mcp_servers config. lark_module port from
# NarraNexus run.sh — change if you've rebound ports locally.
MCP_PROBE_NAME = "lark_module"
MCP_PROBE_URL = "http://localhost:7831/mcp"

# Model that the ChatGPT-account OAuth tier permits (verified
# 2026-06-01). Other gpt-5.4-* / gpt-5.2 are also allowed; codex/o
# series will fail with a 400 on this auth tier.
PROBE_MODEL = "gpt-5.4-mini"


# ---------------- environment + import checks ------------------------


def _print_env_banner() -> None:
    print("=" * 60)
    print("Codex SDK capability spike")
    print("=" * 60)
    print(f"  Python:        {sys.version.split()[0]}")
    try:
        import openai_codex  # noqa: F401  imported for version readout
        ver = getattr(openai_codex, "__version__", "(no __version__ attr)")
        print(f"  openai-codex-sdk: {ver}")
    except ImportError:
        print("  openai-codex-sdk: NOT INSTALLED — `pip install openai-codex-sdk`")
        sys.exit(1)
    print(f"  codex CLI:     {shutil.which('codex') or '(not on PATH)'}")
    auth = Path.home() / ".codex" / "auth.json"
    print(f"  auth.json:     {'present' if auth.exists() else 'MISSING — run `codex login` first'}")
    print(f"  HTTPS_PROXY:   {os.environ.get('HTTPS_PROXY') or '(unset — Test A may hang on CN/HK)'}")
    print()


def _stage_auth(codex_home: Path) -> None:
    src = Path.home() / ".codex" / "auth.json"
    if src.exists():
        shutil.copy(src, codex_home / "auth.json")


# ---------------- event-shape introspection --------------------------
# We don't know the exact shape of streamed events ahead of time (could
# be Pydantic models, dataclasses, or dicts). These helpers probe in a
# defensive order so the script keeps running even if the SDK changes
# its event class names between versions.


def _ev_type(event: object) -> str:
    if hasattr(event, "type"):
        return str(event.type)
    if isinstance(event, dict):
        return str(event.get("type", "(no-type)"))
    return type(event).__name__


def _ev_item_type(event: object) -> str:
    item = getattr(event, "item", None)
    if item is None and isinstance(event, dict):
        item = event.get("item")
    if item is None:
        return "—"
    if hasattr(item, "type"):
        return str(item.type)
    if isinstance(item, dict):
        return str(item.get("type", "(no-type)"))
    return type(item).__name__


# ---------------- Test A: MCP wiring via SDK config dict -------------


async def test_a_mcp_wiring() -> bool:
    """Pass mcp_servers via config dict and ask the agent something
    that should trigger an MCP tool call. PASS = at least one
    ``mcp_tool_call`` (or equivalent) item observed within 60s.
    """
    print("=" * 60)
    print("Test A: MCP wiring via config={'mcp_servers': {...}}")
    print("=" * 60)
    from openai_codex import AsyncCodex, Sandbox

    with tempfile.TemporaryDirectory(prefix="spike_codex_a_") as home_str:
        codex_home = Path(home_str)
        instructions = codex_home / "instructions.md"
        instructions.write_text(
            "You are a probe. You have MCP tools available from the "
            f"'{MCP_PROBE_NAME}' server. When asked what MCP tools "
            "you have, CALL one of them (any will do) to confirm. "
            "Do NOT use Bash; do NOT speculate from memory.\n"
        )
        _stage_auth(codex_home)

        config = {
            "model": PROBE_MODEL,
            "model_instructions_file": str(instructions),
            "mcp_servers": {
                MCP_PROBE_NAME: {"url": MCP_PROBE_URL},
            },
        }
        print(f"  CODEX_HOME = {codex_home}")
        print(f"  config     = {config}")
        print()

        try:
            async with AsyncCodex(
                env={"CODEX_HOME": str(codex_home)},
                config=config,
            ) as codex:
                thread = codex.thread_start(
                    working_directory=str(codex_home),
                    skip_git_repo_check=True,
                    sandbox=Sandbox.workspace_write,
                )
                handle = thread.run_streamed(
                    "What MCP tools do you have? Confirm by calling one."
                )
                mcp_seen = False
                t0 = time.time()
                async for event in handle.events:
                    dt = time.time() - t0
                    et = _ev_type(event)
                    it = _ev_item_type(event)
                    print(f"  [{dt:5.1f}s] event={et:25s} item={it}")
                    if "mcp" in it.lower() or "mcp" in et.lower():
                        mcp_seen = True
                    if dt > 60:
                        print("  [TIMEOUT after 60s]")
                        break
                print()
                if mcp_seen:
                    print("  RESULT: PASS — MCP tool call observed via SDK")
                    return True
                else:
                    print("  RESULT: FAIL/AMBIGUOUS — no MCP item seen")
                    print("    either the agent chose not to call a tool,")
                    print("    or config.mcp_servers didn't reach codex CLI")
                    return False
        except Exception as e:  # noqa: BLE001
            print(f"  RESULT: ERROR — {type(e).__name__}: {e}")
            return False
    print()


# ---------------- Test B: clean cancellation -------------------------


async def test_b_cancellation() -> bool:
    """Spawn a long-running turn, cancel after 3s, measure how long
    until the awaiter releases. PASS = under 5s total.
    """
    print("=" * 60)
    print("Test B: asyncio.CancelledError mid-run")
    print("=" * 60)
    from openai_codex import AsyncCodex, Sandbox

    with tempfile.TemporaryDirectory(prefix="spike_codex_b_") as home_str:
        codex_home = Path(home_str)
        _stage_auth(codex_home)

        try:
            async with AsyncCodex(
                env={"CODEX_HOME": str(codex_home)},
                config={"model": PROBE_MODEL},
            ) as codex:
                thread = codex.thread_start(
                    working_directory=str(codex_home),
                    skip_git_repo_check=True,
                    sandbox=Sandbox.workspace_write,
                )
                handle = thread.run_streamed(
                    "Count slowly from 1 to 50. Use a full sentence "
                    "per number with explanation. Do not stop early."
                )

                event_count = 0

                async def consume() -> None:
                    nonlocal event_count
                    try:
                        async for _ in handle.events:
                            event_count += 1
                    except asyncio.CancelledError:
                        print(f"  consume() caught CancelledError "
                              f"after {event_count} events")
                        raise

                task = asyncio.create_task(consume())
                await asyncio.sleep(3.0)
                print("  cancelling after 3.0s...")
                task.cancel()
                cancel_at = time.time()
                try:
                    await task
                except asyncio.CancelledError:
                    pass
                dt = time.time() - cancel_at
                print(f"  awaiter released {dt:.2f}s after cancel()")
                print()
                if dt < 5.0:
                    print("  RESULT: PASS — clean cancellation under 5s")
                    return True
                else:
                    print(f"  RESULT: FAIL — cancel took {dt:.1f}s "
                          f"(threshold 5s)")
                    return False
        except Exception as e:  # noqa: BLE001
            print(f"  RESULT: ERROR — {type(e).__name__}: {e}")
            return False
    print()


# ---------------- main ----------------------------------------------


async def main() -> None:
    _print_env_banner()
    a = await test_a_mcp_wiring()
    print()
    b = await test_b_cancellation()
    print()
    print("=" * 60)
    print(f"Summary: Test A {'PASS' if a else 'FAIL'} | "
          f"Test B {'PASS' if b else 'FAIL'}")
    print("=" * 60)


if __name__ == "__main__":
    asyncio.run(main())
