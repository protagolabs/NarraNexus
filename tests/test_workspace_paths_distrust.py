"""
@file_name: test_workspace_paths_distrust.py
@author: NetMind.AI
@date: 2026-06-24
@description: T6 — distrust_scratch_path: ephemeral per-room scratch workspace for
distrust IM visitors, isolated from any owner's workspace subtree.
"""
from xyz_agent_context.utils.workspace_paths import (
    agent_workspace_path,
    distrust_scratch_path,
)


def test_scratch_is_outside_owner_subtree():
    base = "/tmp/wsbase"
    owner = agent_workspace_path("agent_x", "owner1", base=base)  # /tmp/wsbase/owner1/agent_x
    scratch = distrust_scratch_path("agent_x", "room1", base=base)
    assert str(scratch).startswith(base)
    assert "owner1" not in str(scratch)
    # under a dedicated scratch root, never a user_id dir
    assert str(scratch) != str(owner)


def test_scratch_sanitizes_matrix_room_id():
    p = distrust_scratch_path("agent_x", "!abc123:matrix.org", base="/b")
    assert ":" not in str(p)
    assert "!" not in str(p)
    assert p.name == "agent_x"  # agent is the leaf dir


def test_scratch_distinct_per_room():
    a = distrust_scratch_path("agent_x", "roomA", base="/b")
    b = distrust_scratch_path("agent_x", "roomB", base="/b")
    assert a != b


def test_scratch_stable_for_same_room():
    a = distrust_scratch_path("agent_x", "roomA", base="/b")
    b = distrust_scratch_path("agent_x", "roomA", base="/b")
    assert a == b


def test_scratch_handles_empty_room_id():
    # Must not produce a path that escapes the scratch root.
    p = distrust_scratch_path("agent_x", "", base="/b")
    assert str(p).startswith("/b")
    assert p.name == "agent_x"
