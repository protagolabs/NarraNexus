"""
@file_name: nexus_loop_vs_claude_bench.py
@author: Bin Liang
@date: 2026-07-29
@description: Same-context, driver-level comparison: nexus_loop vs
claude_code. Runs the SAME materialized messages through both
registered drivers against the SAME provider config (read from the
local ~/.nexusagent/llm_config.json; anthropic-protocol, key-based) and
reports per-run: wall time, time-to-first-event, tool behaviour, usage
and the assembled monologue/output.

Safety: forces a throwaway sqlite DATABASE_URL and local deployment
mode BEFORE importing the platform package (the repo .env points at the
production cluster).

Usage:
    uv run python scripts/spikes/nexus_loop_vs_claude_bench.py [scenario]
    scenario: "chat" (default) | "tool"
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import tempfile
import time
from pathlib import Path

# ---- environment guard BEFORE platform imports -----------------------------
_TMP = tempfile.mkdtemp(prefix="nexus_bench_")
os.environ["DATABASE_URL"] = f"sqlite:///{_TMP}/bench.db"
os.environ["NARRANEXUS_DEPLOYMENT_MODE"] = "local"
os.environ.setdefault("NEXUS_LOOP_INPROCESS", "0")  # exercise the real runner

from xyz_agent_context.agent_framework import get_agent_loop_driver  # noqa: E402
from xyz_agent_context.agent_framework.api_config import (  # noqa: E402
    ClaudeConfig,
    OpenAIConfig,
    set_user_config,
)

SCENARIOS = {
    "chat": (
        "Introduce yourself briefly: who you are and what you can help "
        "with. Keep it under 120 words."
    ),
    "tool": (
        "Create a file named notes/summary.txt containing exactly three "
        "lines: alpha, beta, gamma. Then read it back and confirm the "
        "line count."
    ),
}

SYSTEM_PROMPT = (
    "You are Nova, a capable personal agent. You work inside your own "
    "workspace and use tools to act. Be precise and economical."
)


def _load_provider() -> ClaudeConfig:
    config_path = Path.home() / ".nexusagent" / "llm_config.json"
    data = json.loads(config_path.read_text())
    for provider in data.get("providers", {}).values():
        if (
            provider.get("protocol") == "anthropic"
            and provider.get("auth_type") in ("api_key", "bearer_token")
            and provider.get("api_key")
            and provider.get("is_active")
        ):
            model = (provider.get("models") or [""])[0]
            print(
                f"[bench] provider: {provider.get('name')} model={model} "
                f"auth={provider.get('auth_type')}"
            )
            return ClaudeConfig(
                model=model,
                api_key=provider["api_key"],
                base_url=provider.get("base_url", ""),
                auth_type=provider["auth_type"],
            )
    raise SystemExit("no key-based anthropic-protocol provider in llm_config.json")


async def _run_one(framework: str, prompt: str, claude_cfg: ClaudeConfig) -> dict:
    set_user_config(claude_cfg, OpenAIConfig())
    workspace = Path(_TMP) / framework
    workspace.mkdir(parents=True, exist_ok=True)
    driver = get_agent_loop_driver(framework, working_path=str(workspace))

    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": prompt},
    ]
    stats: dict = {
        "framework": framework,
        "first_event_s": None,
        "tool_calls": [],
        "errors": [],
        "text_chars": 0,
        "thinking_chars": 0,
        "usage": {},
        "events": 0,
    }
    text_parts: list[str] = []
    started = time.perf_counter()
    async for event in driver.agent_loop(messages=messages, mcp_servers={}):
        stats["events"] += 1
        if stats["first_event_s"] is None:
            stats["first_event_s"] = round(time.perf_counter() - started, 3)
        etype = event.get("type")
        data = event.get("data") or {}
        item = event.get("item") or {}
        if etype == "raw_response_event":
            if data.get("type") == "response.text.delta":
                text_parts.append(data.get("delta", ""))
            elif data.get("type") == "response.done":
                stats["usage"] = data.get("usage") or {}
                stats["model"] = data.get("model", "")
            elif data.get("type") == "response.error":
                stats["errors"].append(
                    f"{data.get('error_type')}: {str(data.get('error_message'))[:200]}"
                )
        elif etype == "run_item_stream_event":
            if item.get("type") == "tool_call_item":
                stats["tool_calls"].append(item.get("tool_name"))
            elif item.get("type") == "thinking_item":
                stats["thinking_chars"] += len(item.get("content") or "")
    stats["wall_s"] = round(time.perf_counter() - started, 3)
    stats["text_chars"] = sum(len(t) for t in text_parts)
    stats["output_tail"] = "".join(text_parts)[-400:]
    return stats


async def main() -> None:
    scenario = sys.argv[1] if len(sys.argv) > 1 else "chat"
    prompt = SCENARIOS[scenario]
    claude_cfg = _load_provider()
    print(f"[bench] scenario={scenario!r} workspace={_TMP}")

    results = []
    for framework in ("claude_code", "nexus_loop"):
        print(f"\n===== {framework} =====")
        try:
            result = await _run_one(framework, prompt, claude_cfg)
        except Exception as exc:  # noqa: BLE001 - bench must report, not die
            result = {"framework": framework, "fatal": f"{type(exc).__name__}: {exc}"}
        results.append(result)
        print(json.dumps(result, ensure_ascii=False, indent=2))

    print("\n===== summary =====")
    for r in results:
        if "fatal" in r:
            print(f"{r['framework']:>12}: FATAL {r['fatal']}")
            continue
        usage = r.get("usage") or {}
        print(
            f"{r['framework']:>12}: wall={r['wall_s']}s first={r['first_event_s']}s "
            f"tools={len(r['tool_calls'])} text={r['text_chars']}ch "
            f"think={r['thinking_chars']}ch "
            f"in={usage.get('input_tokens')} out={usage.get('output_tokens')} "
            f"cache_r={usage.get('cache_read_input_tokens')} "
            f"errors={len(r['errors'])}"
        )


if __name__ == "__main__":
    asyncio.run(main())
