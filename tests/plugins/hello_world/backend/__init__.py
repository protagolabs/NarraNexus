"""Hello World — the plugin that exercises every contribution kind (used by the e2e suite)."""
from __future__ import annotations

import asyncio
import hashlib
from pathlib import Path

from fastapi import APIRouter

from narranexus.sdk import (
    BundleSpec,
    ColumnSpec,
    Contribution,
    McpServerSpec,
    RouterSpec,
    SettingField,
    SettingsSchema,
    SkillSpec,
    TableSpec,
    ToolSpec,
    WorkerSpec,
    hookimpl,
    plugin_route_prefix,
)

_ROOT = Path(__file__).resolve().parent.parent
ACTIVATIONS: list[str] = []
HOOK_CALLS: list[str] = []

router = APIRouter()


@router.get("/hello")
async def hello():
    return {"plugin": "acme.hello_world", "message": "hello"}


webhook = APIRouter()


@webhook.post("/ping")
async def ping():
    return {"ok": True}


ROUTES = (
    Contribution("api", lambda: RouterSpec(router, plugin_route_prefix("acme.hello_world"))),
    Contribution("webhook", lambda: RouterSpec(webhook, plugin_route_prefix("acme.hello_world") + "/webhook", auth="none")),
)
TABLES = (
    Contribution(
        "greetings",
        lambda: TableSpec(
            "ext_acme_hello_world_greetings",
            (ColumnSpec("id", "INTEGER", "BIGINT UNSIGNED", nullable=False, primary_key=True, auto_increment=True), ColumnSpec("text", "TEXT", "VARCHAR(255)", nullable=False)),
        ),
    ),
)


class _Handle:
    def __init__(self) -> None:
        self.stopped = False
        self.run = asyncio.sleep(0)

    def stop(self) -> None:
        self.stopped = True


async def _worker_factory(ctx):
    return _Handle()


WORKERS = (Contribution("greeter", lambda: WorkerSpec("greeter", _worker_factory, host="workers")),)


@hookimpl("onDidPersistTurn")
async def after_turn(run_id, agent_id):
    HOOK_CALLS.append(f"{agent_id}:{run_id}")


HOOKIMPLS = (after_turn,)
SETTINGS = (Contribution("schema", lambda: SettingsSchema({"greeting": SettingField("string", default="hi"), "token": SettingField("string", secret=True)})),)


class _Tools:
    def list_tools(self):
        return (ToolSpec("hello_wave", "Wave hello", server="hello_world"), ToolSpec("hello_now", "Always visible", server="hello_world", always_visible=True))


TOOLS = (Contribution("tools", _Tools),)
MCP_SERVERS = (Contribution("hello_world", lambda: McpServerSpec("hello_world", "streamable_http", url="https://example.com/mcp")),)


def _bundle() -> BundleSpec:
    path = _ROOT / "bundles" / "hello.nxbundle"
    return BundleSpec("acme.hello_world.team", path, hashlib.sha256(path.read_bytes()).hexdigest(), "Hello team", "demo")


BUNDLES = (Contribution("team", _bundle),)
SKILLS = (Contribution("hello_skill", lambda: SkillSpec(_ROOT / "skills" / "hello_skill")),)


def activate(ctx):
    ACTIVATIONS.append(ctx.plugin_id)
    ctx.log.info(f"greeting={ctx.settings.get('greeting')}")


def deactivate(ctx):
    ACTIVATIONS.append("deactivated")
