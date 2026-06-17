"""
@file_name: executor_service.py
@author:
@date: 2026-06-17
@description: The agent-loop Executor service.

This is the ONLY tier that spawns the claude/codex CLI. It is a thin,
near-stateless FastAPI app: given an assembled prompt + the resolved
(scoped) provider configs + the workspace path, it runs the LOCAL
agent-loop driver and streams the raw event dicts back as NDJSON.

Security shape (why this exists):
  * It holds NO platform master secrets (no JWT/DB/admin keys). Its
    container is started WITHOUT the platform .env; the only credential
    it sees is the per-run scoped LLM key, which arrives in the request
    body and is applied to a ContextVar for the duration of the loop.
  * It needs NO database — the orchestrator did all DB work (steps
    0-2.5) and ships the assembled messages + configs.
  * Because the executor process does NOT set ``AGENT_EXECUTOR_URL``,
    ``get_agent_loop_driver`` here resolves to the LOCAL claude/codex
    driver (no self-recursion).

Per-agent / per-user workspace isolation is a deployment concern layered
on top (mount only that user's workspace into the container) — out of
scope for this module, which just runs the loop it is handed.
"""
from __future__ import annotations

import json

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, StreamingResponse
from loguru import logger

# Importing the package registers the built-in agent-loop drivers
# (claude_code / codex_cli) into the registry.
import xyz_agent_context.agent_framework  # noqa: F401
from xyz_agent_context.agent_framework.agent_loop_driver import (
    get_agent_loop_driver,
)
from xyz_agent_context.agent_runtime.executor_protocol import (
    apply_provider_configs,
)

app = FastAPI(title="NarraNexus Agent-Loop Executor")


@app.get("/health")
def health() -> dict:
    return {"status": "healthy"}


@app.post("/agent-loop")
async def agent_loop(request: Request) -> StreamingResponse:
    """Run one agent turn and stream raw event dicts back as NDJSON.

    One JSON object per line:
      {"event": {...}}            a raw agent-loop event
      {"error": {"type","message"}}  the loop raised
    """
    body = await request.json()
    framework = body["framework"]
    working_path = body["working_path"]

    # Re-apply the orchestrator's resolved (scoped) provider configs onto
    # THIS task's ContextVars, so the CLI authenticates with the right key.
    apply_provider_configs(body.get("provider_configs") or {})

    # AGENT_EXECUTOR_URL is unset in the executor container → local driver.
    driver = get_agent_loop_driver(framework, working_path=working_path)

    async def _stream():
        logger.info(
            f"[Executor] agent-loop start framework={framework!r} "
            f"workspace={working_path}"
        )
        try:
            async for event in driver.agent_loop(
                messages=body["messages"],
                mcp_server_urls=body.get("mcp_server_urls") or {},
                streaming=bool(body.get("streaming", True)),
                extra_env=body.get("extra_env") or None,
                cancellation=None,  # cancellation = orchestrator aborts the stream
            ):
                yield json.dumps({"event": event}, default=str) + "\n"
        except Exception as e:  # noqa: BLE001 — surface to caller, never crash the service
            logger.exception(f"[Executor] agent-loop failed: {e}")
            yield json.dumps(
                {"error": {"type": type(e).__name__, "message": str(e)}}
            ) + "\n"

    return StreamingResponse(_stream(), media_type="application/x-ndjson")


def main() -> None:
    import os

    import uvicorn

    port = int(os.environ.get("AGENT_EXECUTOR_PORT", "8020"))
    uvicorn.run(app, host="0.0.0.0", port=port)


if __name__ == "__main__":
    main()
