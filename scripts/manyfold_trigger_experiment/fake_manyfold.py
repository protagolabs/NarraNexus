#!/usr/bin/env python3
"""
@file_name: fake_manyfold.py
@author: rujing.yan
@date: 2026-07-20
@description: Local stand-in for the Manyfold host, to validate that
NarraNexus's trigger surface can be moved out to an external platform.

This is the experiment harness for "trigger 走 Manyfold" (model B). It plays
the two roles Manyfold takes over from a suspended sandbox — the "ears" (IM
inbound) and the "clock" (job scheduling) — by speaking the exact HTTP
contracts NarraNexus exposes (PR #118 + the model-B inbound extension). It
does NOT reproduce Firecracker/sprites/suspend-wake; the abstract event flow is
what we validate, per "本地无法 1:1 模拟,抽象逻辑一致即可".

Contracts exercised (all gateway-token authed):
  - GET  /manyfold/jobs      — pull the authoritative job inventory
  - GET  /manyfold/channels  — pull IM bindings + decoded credentials
  - POST /v1/chat/completions with `[[nx:run_job <id> v1]]`  — fire a job
  - POST /v1/chat/completions with channel_provider/channel_context — forward
    an IM inbound so the agent replies via its LOCAL channel tool (model B)
  - a tiny HTTP server receiving the config-change notify webhook

Config via env: NEXUS_BASE_URL (default http://localhost:8000),
MANYFOLD_GATEWAY_TOKEN (must match the running Nexus).

Usage:
  python fake_manyfold.py pull-jobs
  python fake_manyfold.py pull-channels
  python fake_manyfold.py fire-job   --agent <agent_id> --job <job_id>
  python fake_manyfold.py send-im    --agent <agent_id> --provider lark \\
                                     --room oc_test --sender ou_alice \\
                                     --sender-name Alice --text "weather tomorrow?"
  python fake_manyfold.py serve-notify --port 9099
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any, Optional

import httpx

BASE_URL = os.environ.get("NEXUS_BASE_URL", "http://localhost:8000").rstrip("/")
TOKEN = os.environ.get("MANYFOLD_GATEWAY_TOKEN", "")

# ANSI helpers for a readable PASS/FAIL surface.
_GREEN, _RED, _DIM, _RESET = "\033[32m", "\033[31m", "\033[2m", "\033[0m"


def _client() -> httpx.Client:
    if not TOKEN:
        _die("MANYFOLD_GATEWAY_TOKEN is not set (must match the running Nexus).")
    return httpx.Client(
        base_url=BASE_URL,
        headers={"Authorization": f"Bearer {TOKEN}"},
        timeout=120.0,
    )


def _die(msg: str) -> None:
    print(f"{_RED}✗ {msg}{_RESET}", file=sys.stderr)
    sys.exit(1)


def _ok(msg: str) -> None:
    print(f"{_GREEN}✓ {msg}{_RESET}")


# ---------------------------------------------------------------------------
# The "clock" — pull inventory, fire jobs
# ---------------------------------------------------------------------------


def pull_jobs() -> list[dict]:
    with _client() as c:
        r = c.get("/manyfold/jobs")
    r.raise_for_status()
    data = r.json().get("data", [])
    print(f"{_DIM}GET /manyfold/jobs → {len(data)} non-terminal job(s){_RESET}")
    for j in data:
        print(
            f"  · {j.get('job_id')}  agent={j.get('agent_id')}  "
            f"status={j.get('status')}  next_run={j.get('next_run_time')}"
        )
    return data


def pull_channels() -> list[dict]:
    with _client() as c:
        r = c.get("/manyfold/channels")
    r.raise_for_status()
    data = r.json().get("data", [])
    print(f"{_DIM}GET /manyfold/channels → {len(data)} binding(s){_RESET}")
    for ch in data:
        print(
            f"  · {ch.get('provider')}  agent={ch.get('agent_id')}  "
            f"enabled={ch.get('enabled')}  external_id={ch.get('external_id')}"
        )
    return data


def fire_job(agent_id: str, job_id: str) -> None:
    """Simulate a mirrored alarm firing: send the run-job control message and
    read back the JobTrigger execution outcome.

    Uses stream=True — the real Manyfold alarm call streams, and the run-job
    dispatch emits a 15s empty-content heartbeat so a long run (target job +
    bounded drain of other due jobs) never trips a proxy / read timeout."""
    control = f"[[nx:run_job {job_id} v1]]"
    print(f"{_DIM}POST /v1/chat/completions  model={agent_id}  '{control}'  (stream){_RESET}")
    body = {
        "model": agent_id,
        "messages": [{"role": "user", "content": control}],
        "stream": True,
    }
    content = ""
    with _client() as c:
        with c.stream("POST", "/v1/chat/completions", json=body) as r:
            if r.status_code != 200:
                _die(f"HTTP {r.status_code}: {r.read().decode()[:300]}")
            for chunk in _iter_sse_content(r):
                content += chunk
    content = content.strip()
    if content.startswith("nx:run_job") and " ok " in f" {content} ":
        _ok(f"job executed → {content}")
    else:
        print(f"{_RED}✗ unexpected run-job outcome → {content!r}{_RESET}")


def _iter_sse_content(response: httpx.Response):
    """Yield delta.content strings from an OpenAI-shaped SSE stream."""
    for line in response.iter_lines():
        if not line or not line.startswith("data: "):
            continue
        data = line[len("data: "):]
        if data.strip() == "[DONE]":
            break
        try:
            delta = json.loads(data)["choices"][0]["delta"]
        except (json.JSONDecodeError, KeyError, IndexError):
            continue
        piece = delta.get("content")
        if piece:
            yield piece


# ---------------------------------------------------------------------------
# The "ears" — forward an IM inbound (model B)
# ---------------------------------------------------------------------------


def send_im(
    agent_id: str,
    provider: str,
    room: str,
    sender: str,
    sender_name: str,
    text: str,
) -> None:
    """Forward an inbound IM message with channel context and assert the agent
    replies via its LOCAL channel tool (model B) targeting the right room —
    NOT via send_message_to_user_directly (which would be model A)."""
    body = {
        "model": agent_id,
        "messages": [{"role": "user", "content": text}],
        "stream": False,
        "channel_provider": provider,
        "channel_context": {
            "room_id": room,
            "sender_id": sender,
            "sender_name": sender_name,
            "source_message_id": "om_experiment_1",
        },
    }
    print(
        f"{_DIM}POST /v1/chat/completions  model={agent_id}  "
        f"provider={provider} room={room} sender={sender}{_RESET}"
    )
    with _client() as c:
        r = c.post("/v1/chat/completions", json=body)
    if r.status_code != 200:
        _die(f"HTTP {r.status_code}: {r.text[:400]}")

    msg = _first_message(r.json())
    tool_calls = msg.get("tool_calls") or []
    content = msg.get("content") or ""

    # The local channel reply tool for this provider (model B).
    local_tool = {
        "lark": "lark_cli",
        "slack": "slack_cli",
        "telegram": "tg_cli",
        "wechat": "wechat_send",
        "discord": "discord_send",
    }.get(provider, "")

    print(f"{_DIM}  tool_calls: {[_tc_name(t) for t in tool_calls]}{_RESET}")
    if content:
        print(f"{_DIM}  delta.content (model-A path): {content[:120]!r}{_RESET}")

    matched = _find_local_reply(tool_calls, local_tool, room)
    if matched:
        _ok(
            f"model B confirmed: agent replied via LOCAL {local_tool} to the "
            f"right room ({room}).\n    call: {matched[:160]}"
        )
    else:
        print(
            f"{_RED}✗ no local {local_tool!r} reply targeting room {room!r} found. "
            f"Agent may have used send_message_to_user_directly (model A) or lacks "
            f"channel context / a bound credential.{_RESET}"
        )


def _find_local_reply(
    tool_calls: list[dict], local_tool: str, room: str
) -> Optional[str]:
    """Return the matching reply-tool call string, or None. Matches any tool
    whose name contains the provider's local reply tool and whose arguments
    carry a send command aimed at the room."""
    for tc in tool_calls:
        name = _tc_name(tc)
        if local_tool and local_tool not in name:
            continue
        args = _tc_args(tc)
        blob = json.dumps(args, ensure_ascii=False)
        if room in blob and ("messages-send" in blob or "messages-reply" in blob or "send" in name):
            return f"{name}({blob})"
    return None


def _tc_name(tc: dict) -> str:
    return (tc.get("function") or {}).get("name", "") or tc.get("name", "")


def _tc_args(tc: dict) -> Any:
    raw = (tc.get("function") or {}).get("arguments")
    if isinstance(raw, str):
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            return {"_raw": raw}
    return raw or {}


def _first_message(resp: dict) -> dict:
    choices = resp.get("choices") or [{}]
    return choices[0].get("message") or {}


# ---------------------------------------------------------------------------
# Config-change notify receiver (the webhook Nexus fires after a config write)
# ---------------------------------------------------------------------------


class _NotifyHandler(BaseHTTPRequestHandler):
    def do_POST(self) -> None:  # noqa: N802 (stdlib naming)
        length = int(self.headers.get("Content-Length", 0))
        raw = self.rfile.read(length) if length else b""
        try:
            payload = json.loads(raw or b"{}")
        except json.JSONDecodeError:
            payload = {"_raw": raw.decode("utf-8", "replace")}
        auth = self.headers.get("Authorization", "")
        print(f"{_GREEN}▶ notify{_RESET} kinds={payload.get('kinds')} "
              f"runtimeId={payload.get('runtimeId')} auth={'yes' if auth else 'no'}")
        # A real Manyfold would now re-pull; show that it can.
        if TOKEN:
            try:
                pull_jobs()
            except Exception as e:  # noqa: BLE001 — best-effort demo pull
                print(f"{_DIM}  (re-pull failed: {e}){_RESET}")
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b'{"ok":true}')

    def log_message(self, *_args) -> None:  # silence default access logging
        pass


def serve_notify(port: int) -> None:
    print(f"fake Manyfold notify receiver on http://localhost:{port}/notify")
    print(f"{_DIM}point MANYFOLD_SYNC_WEBHOOK_URL at it; Ctrl-C to stop{_RESET}")
    HTTPServer(("0.0.0.0", port), _NotifyHandler).serve_forever()


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main() -> None:
    p = argparse.ArgumentParser(description="Local Manyfold stand-in for trigger validation")
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("pull-jobs")
    sub.add_parser("pull-channels")

    fj = sub.add_parser("fire-job")
    fj.add_argument("--agent", required=True)
    fj.add_argument("--job", required=True)

    si = sub.add_parser("send-im")
    si.add_argument("--agent", required=True)
    si.add_argument("--provider", default="lark")
    si.add_argument("--room", required=True)
    si.add_argument("--sender", required=True)
    si.add_argument("--sender-name", default="")
    si.add_argument("--text", required=True)

    sn = sub.add_parser("serve-notify")
    sn.add_argument("--port", type=int, default=9099)

    args = p.parse_args()
    if args.cmd == "pull-jobs":
        pull_jobs()
    elif args.cmd == "pull-channels":
        pull_channels()
    elif args.cmd == "fire-job":
        fire_job(args.agent, args.job)
    elif args.cmd == "send-im":
        send_im(
            args.agent, args.provider, args.room, args.sender,
            args.sender_name or args.sender, args.text,
        )
    elif args.cmd == "serve-notify":
        serve_notify(args.port)


if __name__ == "__main__":
    main()
