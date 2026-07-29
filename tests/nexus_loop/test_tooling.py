"""
@file_name: test_tooling.py
@author: Bin Liang
@date: 2026-07-29
@description: Tooling group: policy fail-closed semantics, workspace
confinement, builtin file/shell tools against a real tmp workspace,
dispatcher routing + marker short-circuit + allow/deny filters,
capability expansion.
"""

import pytest

from xyz_agent_context.agent_framework.nexus_loop.contracts.model import McpServerSpec
from xyz_agent_context.agent_framework.nexus_loop.contracts.tooling import (
    PolicyContext,
    ToolAnnotations,
    ToolCall,
    ToolContext,
    ToolResult,
    ToolSpec,
)
from xyz_agent_context.agent_framework.nexus_loop._nexus_loop_impl.tooling.builtin import (
    BuiltinToolset,
)
from xyz_agent_context.agent_framework.nexus_loop._nexus_loop_impl.tooling.dispatcher import (
    ToolDispatcher,
)
from xyz_agent_context.agent_framework.nexus_loop._nexus_loop_impl.tooling.expansion import (
    CapabilityExpander,
    Expandable,
)
from xyz_agent_context.agent_framework.nexus_loop._nexus_loop_impl.tooling.policy import (
    DisallowedToolsLayer,
    PolicyEngine,
    WorkspaceConfinementLayer,
)


@pytest.fixture()
def workspace(tmp_path):
    (tmp_path / "hello.txt").write_text("line1\nline2\nline3\n")
    return tmp_path


@pytest.fixture()
def ctx(workspace):
    return ToolContext(agent_id="a1", workspace=str(workspace))


@pytest.fixture()
def engine():
    return PolicyEngine((DisallowedToolsLayer(), WorkspaceConfinementLayer()))


def _pctx(ctx, disallowed=()):
    return PolicyContext(tool_ctx=ctx, disallowed_tools=frozenset(disallowed))


def test_policy_disallow_and_confinement(engine, ctx):
    ok = engine.check(ToolCall(id="1", name="read_file", args={"path": "hello.txt"}), _pctx(ctx))
    assert ok.allowed
    denied = engine.check(
        ToolCall(id="2", name="read_file", args={"path": "/etc/passwd"}), _pctx(ctx)
    )
    assert not denied.allowed and "outside the workspace" in denied.reason
    escape = engine.check(
        ToolCall(id="3", name="read_file", args={"path": "../../etc/passwd"}), _pctx(ctx)
    )
    assert not escape.allowed
    banned = engine.check(
        ToolCall(id="4", name="bash", args={"command": "ls"}), _pctx(ctx, ["bash"])
    )
    assert not banned.allowed


def test_policy_layer_crash_is_deny(ctx):
    class Broken:
        def check(self, call, ctx):
            raise RuntimeError("boom")

    engine = PolicyEngine((Broken(),))
    decision = engine.check(ToolCall(id="1", name="x", args={}), _pctx(ctx))
    assert not decision.allowed and "fail-closed" in decision.reason


@pytest.mark.asyncio
async def test_builtin_file_tools_roundtrip(ctx):
    toolset = BuiltinToolset(ctx, enabled_groups=frozenset({"files", "shell"}))
    names = {s.name for s in toolset.list_tools()}
    assert {"read_file", "write_file", "edit_file", "glob", "grep", "ls", "bash"} <= names

    read = await toolset.call("read_file", {"path": "hello.txt", "offset": 2, "limit": 1}, ctx)
    assert read.ok and read.content == "line2"

    write = await toolset.call("write_file", {"path": "sub/new.txt", "content": "abc"}, ctx)
    assert write.ok
    edit = await toolset.call("edit_file", {"path": "sub/new.txt", "old": "b", "new": "B"}, ctx)
    assert edit.ok
    read2 = await toolset.call("read_file", {"path": "sub/new.txt"}, ctx)
    assert read2.content == "aBc"

    grep = await toolset.call("grep", {"pattern": "line2"}, ctx)
    assert grep.ok and "hello.txt:2:" in grep.content

    bash = await toolset.call("bash", {"command": "echo hi && pwd"}, ctx)
    assert bash.ok and "hi" in bash.content and ctx.workspace in bash.content


@pytest.mark.asyncio
async def test_bash_failure_and_timeout(ctx):
    toolset = BuiltinToolset(ctx, enabled_groups=frozenset({"shell"}))
    fail = await toolset.call("bash", {"command": "exit 3"}, ctx)
    assert not fail.ok and "exit code 3" in (fail.error or "")
    slow = await toolset.call("bash", {"command": "sleep 5", "timeout_s": 1}, ctx)
    assert not slow.ok and "timed out" in (slow.error or "")


class _StubChannel:
    """A fake channel with a marker tool and a normal tool."""

    generation = 0

    def __init__(self):
        self.calls = []

    def list_tools(self):
        return [
            ToolSpec(
                name="mcp__chat__send_message_to_user_directly",
                description="Reply to the user.",
                input_schema={"type": "object"},
                annotations=ToolAnnotations(
                    expressive=True, marker_only=True, streamable_fields=("content",)
                ),
            ),
            ToolSpec(name="zeta_tool", description="does z", input_schema={"type": "object"}),
        ]

    async def call(self, name, args, ctx):
        self.calls.append(name)
        return ToolResult(call_id="", ok=True, content=f"ran {name}")

    async def refresh(self):
        return False


@pytest.mark.asyncio
async def test_dispatcher_routing_marker_and_filters(ctx, engine):
    builtin = BuiltinToolset(ctx, enabled_groups=frozenset({"files"}))
    stub = _StubChannel()
    dispatcher = ToolDispatcher(
        (builtin, stub),
        policy=engine,
        ctx=ctx,
        disallowed_tools=frozenset({"grep"}),
    )
    names = [s.name for s in dispatcher.visible_tools()]
    assert "grep" not in names            # disallowed filtered from schema
    assert "zeta_tool" in names

    # Marker tool short-circuits: channel never invoked.
    marker = await dispatcher.execute(
        ToolCall(id="c1", name="mcp__chat__send_message_to_user_directly",
                 args={"content": "hi"})
    )
    assert marker.ok and stub.calls == []

    routed = await dispatcher.execute(ToolCall(id="c2", name="zeta_tool", args={}))
    assert routed.ok and routed.call_id == "c2" and stub.calls == ["zeta_tool"]

    denied = await dispatcher.execute(ToolCall(id="c3", name="grep", args={"pattern": "x"}))
    assert not denied.ok and "denied" in (denied.error or "")

    missing = await dispatcher.execute(ToolCall(id="c4", name="ghost", args={}))
    assert not missing.ok

    lines = dispatcher.search_lines("zeta")
    assert any("zeta_tool" in line for line in lines)


@pytest.mark.asyncio
async def test_capability_expander_idempotent_and_seams():
    attached, env = [], {}

    async def add_servers(servers):
        attached.append(servers)

    expander = CapabilityExpander(
        (
            Expandable(
                key="jobs",
                card="schedule recurring work",
                instructions="Use job tools wisely.",
                mcp_servers={"job": McpServerSpec(url="http://x/sse")},
                extra_env={"JOB": "1"},
            ),
            Expandable(key="empty", card="nothing much"),
        ),
        add_mcp_servers=add_servers,
        add_env=env.update,
    )
    assert "jobs: schedule recurring work" in expander.card_index()

    text = await expander.expand("jobs")
    assert text == "Use job tools wisely."
    assert len(attached) == 1 and env == {"JOB": "1"}

    again = await expander.expand("jobs")   # idempotent: no re-attach,
    assert len(attached) == 1 and again == text  # instructions re-served

    with pytest.raises(KeyError):
        await expander.expand("nope")

    block = await expander.expand_initial(frozenset({"empty"}))
    assert "now active" in block
    assert expander.expanded_keys() == frozenset({"jobs", "empty"})
