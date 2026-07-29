"""
@file_name: nexus_agent.py
@author: Bin Liang
@date: 2026-07-29
@description: NexusAgent — the AgentLoopDriver for the home-grown
nexus_loop framework (structurally the twin of adapters/claude: the
adapter translates contracts and owns zero business logic).

Three jobs only:
  1. legacy call shape (messages, mcp_servers, streaming, extra_env,
     cancellation, **kwargs) → ``TurnRequest`` (model settings read from
     the SAME per-turn ``claude_config`` the claude driver uses — the
     platform's provider configs are Anthropic-protocol endpoints);
  2. run the turn in its OWN PROCESS by default (the runner; in-process
     mode via ``NEXUS_LOOP_INPROCESS=1`` for the executor container and
     tests) and relay its NDJSON — with manual line buffering, never a
     line-length assumption;
  3. guarantee the legacy stream ends with exactly one
     ``response.done`` on every path (the billing chain's sole source).

Platform adaptation seams accepted via **kwargs (forward-compatible,
optional): ``expressive_tools``, ``marker_tools``, ``expandables``,
``initial_expansions``, ``agent_id``.
"""

from __future__ import annotations

import asyncio
import json
import os
import signal
import sys
import uuid
from typing import Any, AsyncGenerator

from loguru import logger

from xyz_agent_context.agent_framework.api_config import claude_config
from xyz_agent_context.agent_framework.loop.cancellation_view import CancellationView
from xyz_agent_context.agent_framework.loop.events import (
    DATA_TYPE_DONE,
    TYPE_RAW_RESPONSE_EVENT,
    raw_error_event,
)
from xyz_agent_context.utils.logging import timed

_STREAM_LIMIT_BYTES = 32 * 1024 * 1024  # image-bearing lines reach 100s of KB
_CANCEL_POLL_S = 0.2


class NexusAgent:
    """AgentLoopDriver implementation for framework name ``nexus_loop``."""

    def __init__(self, working_path: str = "./"):
        self.working_path = working_path

    def capabilities(self) -> set[str]:
        """Shipped beyond the base contract: the two-track event log
        (local NDJSON truth file per turn)."""
        return {"event_log"}

    @timed("llm.nexus.agent_loop", slow_threshold_ms=15000)
    async def agent_loop(
        self,
        messages: list[dict[str, Any]],
        mcp_servers: dict[str, dict[str, Any]],  # {name: {"url": str, "headers": {str: str}?}}
        *,
        streaming: bool = True,
        extra_env: dict[str, str] | None = None,
        cancellation: Any | None = None,
        **kwargs: Any,
    ) -> AsyncGenerator[dict[str, Any], None]:
        cancel = CancellationView(cancellation)
        done_seen = False
        try:
            request_payload = self._build_request_payload(
                messages, mcp_servers, extra_env, kwargs
            )
            if os.getenv("NEXUS_LOOP_INPROCESS") == "1":
                events = self._run_inprocess(request_payload, cancel)
            else:
                events = self._run_subprocess(request_payload, cancel)
            async for event in events:
                if _is_done(event):
                    done_seen = True
                yield event
        except Exception as exc:  # noqa: BLE001 - classified for the wire
            from xyz_agent_context.agent_framework.nexus_loop._nexus_loop_impl.session.error_classifier import (
                DefaultErrorClassifier,
            )

            error = DefaultErrorClassifier().classify(exc)
            logger.exception(f"[nexus_loop] turn failed: {error!r}")
            from typing import cast as _cast

            yield _cast(
                dict[str, Any], raw_error_event(error.message, error.legacy_error_type())
            )
        finally:
            if not done_seen:
                # Every path pays the billing chain exactly once.
                yield {
                    "type": TYPE_RAW_RESPONSE_EVENT,
                    "data": {"type": DATA_TYPE_DONE, "usage": {},
                             "stop_reason": "error",
                             "model": claude_config.model or ""},
                }

    # ------------------------------------------------------------------

    def _build_request_payload(
        self,
        messages: list[dict[str, Any]],
        mcp_servers: dict[str, dict[str, Any]],
        extra_env: dict[str, str] | None,
        kwargs: dict[str, Any],
    ) -> dict[str, Any]:
        if claude_config.auth_type in ("oauth", "oauth_token"):
            raise ValueError(
                "nexus_loop drives the provider API directly and cannot use "
                "subscription OAuth credentials; keep this agent on the "
                "claude_code framework or configure an API-key provider"
            )
        expressive = tuple(kwargs.get("expressive_tools") or ()) or tuple(
            name
            for name in _reply_tool_names(mcp_servers)
        )
        llm_extra: dict[str, Any] = {}
        if claude_config.auth_type == "bearer_token" and claude_config.api_key:
            # Anthropic-protocol gateways expecting Authorization: Bearer
            # (litellm's anthropic route sends x-api-key; add the header).
            llm_extra["extra_headers"] = {
                "Authorization": f"Bearer {claude_config.api_key}"
            }
        options: dict[str, Any] = {
            "cwd": self.working_path,
            "agent_id": str(kwargs.get("agent_id") or "agent"),
            "env": dict(extra_env or {}),
            "model": claude_config.model or "",
            "provider": "anthropic",
            "api_key": claude_config.api_key or "",
            "base_url": claude_config.base_url or "",
            "llm_extra": llm_extra,
            "thinking": bool(getattr(claude_config, "thinking", "") == "enabled"),
            "mcp_servers": mcp_servers,
            "disallowed_tools": tuple(kwargs.get("disallowed_tools") or ()),
            "expressive_tools": expressive,
            "marker_tools": tuple(kwargs.get("marker_tools") or ()),
            "expandables": tuple(kwargs.get("expandables") or ()),
            "initial_expansions": sorted(kwargs.get("initial_expansions") or ()),
            "output_mode": "legacy_dict",
        }
        return {
            "thread_id": f"turn_{uuid.uuid4().hex[:12]}",
            "messages": messages,
            "options": options,
        }

    async def _run_inprocess(
        self, payload: dict[str, Any], cancel: CancellationView
    ) -> AsyncGenerator[dict[str, Any], None]:
        """Same code path as the runner, minus the process boundary
        (executor containers and tests — already isolated)."""
        from xyz_agent_context.agent_framework.nexus_loop.runner import serve_turn

        queue: asyncio.Queue[dict[str, Any] | None] = asyncio.Queue()

        async def write_line(obj: dict[str, Any]) -> None:
            await queue.put(obj)

        async def _serve() -> None:
            try:
                await serve_turn(json.dumps(payload, default=_json_default), write_line)
            finally:
                await queue.put(None)

        # Bridge the platform token into the runner's cancellation view.
        class _Bridge:
            def set(self) -> None:  # runner's signal-handler hook
                return None

            def requested(self) -> bool:
                return cancel.requested()

        import xyz_agent_context.agent_framework.nexus_loop.runner as runner_module

        original = runner_module._SignalCancellation
        runner_module._SignalCancellation = lambda: _Bridge()  # type: ignore[assignment]
        try:
            task = asyncio.create_task(_serve())
            while True:
                line = await queue.get()
                if line is None:
                    break
                event = self._line_to_event(line)
                if event is not None:
                    yield event
            await task
        finally:
            runner_module._SignalCancellation = original  # type: ignore[assignment]

    async def _run_subprocess(
        self, payload: dict[str, Any], cancel: CancellationView
    ) -> AsyncGenerator[dict[str, Any], None]:
        process = await asyncio.create_subprocess_exec(
            sys.executable,
            "-m",
            "xyz_agent_context.agent_framework.nexus_loop.runner",
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            limit=_STREAM_LIMIT_BYTES,
            start_new_session=True,
        )
        assert process.stdin and process.stdout
        process.stdin.write(
            (json.dumps(payload, default=_json_default) + "\n").encode("utf-8")
        )
        await process.stdin.drain()
        process.stdin.close()

        signalled = False
        try:
            while True:
                read_task = asyncio.ensure_future(process.stdout.readline())
                while not read_task.done():
                    await asyncio.wait({read_task}, timeout=_CANCEL_POLL_S)
                    if cancel.requested() and not signalled:
                        signalled = True
                        _terminate_group(process.pid)
                raw = read_task.result()
                if not raw:
                    break
                try:
                    line = json.loads(raw.decode("utf-8"))
                except json.JSONDecodeError:
                    logger.warning(f"[nexus_loop] non-JSON runner line: {raw[:200]!r}")
                    continue
                event = self._line_to_event(line)
                if event is not None:
                    yield event
            await process.wait()
            if process.returncode not in (0, None) and process.stderr:
                stderr_tail = (await process.stderr.read())[-2000:].decode(
                    "utf-8", errors="replace"
                )
                if stderr_tail.strip():
                    logger.warning(f"[nexus_loop] runner stderr tail: {stderr_tail}")
        finally:
            if process.returncode is None:
                _terminate_group(process.pid)
                await process.wait()

    @staticmethod
    def _line_to_event(line: dict[str, Any]) -> dict[str, Any] | None:
        if "event" in line:
            return line["event"]
        exit_info = line.get("exit")
        if isinstance(exit_info, dict) and not exit_info.get("ok", True):
            trace = exit_info.get("traceback")
            if trace:
                logger.warning(f"[nexus_loop] runner traceback:\n{trace}")
            raise RuntimeError(str(exit_info.get("error") or "nexus_loop runner failed"))
        return None


def _reply_tool_names(mcp_servers: dict[str, dict[str, Any]]) -> list[str]:
    """The platform's reply tools, derived from server names (the legacy
    substring contract): any chat-ish server exposes the direct-reply
    tool under the mcp__ namespace."""
    return [
        f"mcp__{name}__send_message_to_user_directly"
        for name in mcp_servers
        if "chat" in name
    ]


def _terminate_group(pid: int) -> None:
    try:
        os.killpg(os.getpgid(pid), signal.SIGTERM)
    except (ProcessLookupError, PermissionError):
        pass


def _is_done(event: dict[str, Any]) -> bool:
    return (
        event.get("type") == TYPE_RAW_RESPONSE_EVENT
        and (event.get("data") or {}).get("type") == DATA_TYPE_DONE
    )


def _json_default(value: Any) -> Any:
    if isinstance(value, (set, frozenset, tuple)):
        return list(value)
    return str(value)
