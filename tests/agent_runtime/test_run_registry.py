"""
@file_name: test_run_registry.py
@author: Bin Liang
@date: 2026-08-21
@description: RunRegistry — the per-process index of live runs by
(agent_id, surface), so a producer can ask "is this agent already running
on THIS surface?" and steer into that run instead of dispatching a new
turn. Surface-scoping is the whole point: a message for one surface must
never route into the same agent's run on a different surface.
"""

import pytest

from xyz_agent_context.agent_runtime.run_registry import RunRegistry


@pytest.mark.asyncio
async def test_register_then_live_run_returns_the_handle():
    reg = RunRegistry()
    reg.register("agent_a", "team:room1", "run1", steer="handle1")

    live = reg.live_run("agent_a", "team:room1")
    assert live is not None
    assert live.run_id == "run1"
    assert live.steer == "handle1"


@pytest.mark.asyncio
async def test_a_different_surface_of_the_same_agent_does_not_match():
    # The anti-cross-talk guarantee: agent_a running on team room1 must NOT
    # be a match for a message on team room2 (or its web chat, or a job).
    reg = RunRegistry()
    reg.register("agent_a", "team:room1", "run1", steer="h")

    assert reg.live_run("agent_a", "team:room2") is None
    assert reg.live_run("agent_a", "chat:u1:s1") is None


@pytest.mark.asyncio
async def test_a_different_agent_does_not_match():
    reg = RunRegistry()
    reg.register("agent_a", "team:room1", "run1", steer="h")
    assert reg.live_run("agent_b", "team:room1") is None


@pytest.mark.asyncio
async def test_release_removes_the_run():
    reg = RunRegistry()
    reg.register("agent_a", "team:room1", "run1", steer="h")
    reg.release("run1")
    assert reg.live_run("agent_a", "team:room1") is None


@pytest.mark.asyncio
async def test_concurrent_runs_of_one_agent_on_distinct_surfaces_are_isolated():
    # The multi-team / multi-trigger case: one agent, several live runs, each
    # found only by its own surface — three isolated inboxes downstream.
    reg = RunRegistry()
    reg.register("agent_a", "team:roomA", "run_a", steer="ha")
    reg.register("agent_a", "team:roomB", "run_b", steer="hb")
    reg.register("agent_a", "chat:u1:s1", "run_web", steer="hw")

    assert reg.live_run("agent_a", "team:roomA").run_id == "run_a"
    assert reg.live_run("agent_a", "team:roomB").run_id == "run_b"
    assert reg.live_run("agent_a", "chat:u1:s1").run_id == "run_web"


@pytest.mark.asyncio
async def test_releasing_one_run_leaves_the_agents_other_runs():
    reg = RunRegistry()
    reg.register("agent_a", "team:roomA", "run_a", steer="ha")
    reg.register("agent_a", "team:roomB", "run_b", steer="hb")

    reg.release("run_a")

    assert reg.live_run("agent_a", "team:roomA") is None
    assert reg.live_run("agent_a", "team:roomB").run_id == "run_b"


@pytest.mark.asyncio
async def test_re_registering_a_surface_replaces_the_stale_run():
    # A surface holds at most one live run; the newest wins and the stale
    # mapping never shadows it.
    reg = RunRegistry()
    reg.register("agent_a", "team:roomA", "run_old", steer="old")
    reg.register("agent_a", "team:roomA", "run_new", steer="new")

    live = reg.live_run("agent_a", "team:roomA")
    assert live.run_id == "run_new"
    assert live.steer == "new"


@pytest.mark.asyncio
async def test_releasing_a_superseded_run_does_not_evict_the_current_one():
    # run_old was replaced by run_new on the same surface; a late release of
    # run_old must not clear run_new's mapping.
    reg = RunRegistry()
    reg.register("agent_a", "team:roomA", "run_old", steer="old")
    reg.register("agent_a", "team:roomA", "run_new", steer="new")

    reg.release("run_old")

    assert reg.live_run("agent_a", "team:roomA").run_id == "run_new"


@pytest.mark.asyncio
async def test_registered_scope_releases_on_normal_and_exception_exit():
    reg = RunRegistry()
    with reg.registered("agent_a", "team:room1", "run1", steer="h"):
        assert reg.live_run("agent_a", "team:room1").run_id == "run1"
    assert reg.live_run("agent_a", "team:room1") is None  # released on normal exit

    with pytest.raises(ValueError):
        with reg.registered("agent_a", "team:room1", "run2", steer="h"):
            assert reg.live_run("agent_a", "team:room1").run_id == "run2"
            raise ValueError("boom")
    # finally released even though the body raised — a crashed run cannot pin
    # the surface (the deaf-surface guard).
    assert reg.live_run("agent_a", "team:room1") is None


@pytest.mark.asyncio
async def test_live_run_sweeps_a_dead_run_so_the_surface_is_not_deaf_forever():
    reg = RunRegistry()
    reg.register("agent_a", "team:room1", "run1", steer="h", is_alive=lambda: False)
    # The run says it is dead → live_run reports no live run AND clears the
    # stale mapping, so the next message dispatches a fresh turn.
    assert reg.live_run("agent_a", "team:room1") is None
    assert ("agent_a", "team:room1") not in reg._by_surface


@pytest.mark.asyncio
async def test_superseding_a_run_does_not_leak_the_old_by_run_entry():
    reg = RunRegistry()
    reg.register("agent_a", "team:roomA", "run_old", steer="old")
    reg.register("agent_a", "team:roomA", "run_new", steer="new")
    # The old run's _by_run entry (and its SteerChannel) must be dropped, not
    # left pinned for the life of the process. Delete the supersede pop and
    # this is 2.
    assert len(reg._by_run) == 1
    assert "run_old" not in reg._by_run
