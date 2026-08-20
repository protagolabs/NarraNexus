"""
@file_name: test_admission_blocklist.py
@date: 2026-08-10
@description: #2 executor abuse blocklist — the admission-side gate.

Locks: a blocked user_id is rejected IMMEDIATELY at acquire() with
AccessDeniedError (never queued, distinct from the concurrency gate that
only delays); a non-blocked user is admitted normally; the blocklist file
hot-reloads by mtime; and the local default (no file configured) blocks
nobody so `bash run.sh` / the DMG are unaffected (binding rule #7).
"""
from __future__ import annotations

import os
import time

import pytest

import xyz_agent_context.agent_runtime.admission as adm
from xyz_agent_context.agent_runtime.admission import AgentAdmissionController


def _use_blocklist(monkeypatch, path):
    monkeypatch.setattr(adm, "_BLOCKLIST_FILE", path)
    monkeypatch.setattr(adm, "_blocklist", set())
    monkeypatch.setattr(adm, "_blocklist_mtime", -1.0)


@pytest.mark.asyncio
async def test_blocked_user_rejected_immediately(tmp_path, monkeypatch):
    bl = tmp_path / "users.txt"
    bl.write_text("# a comment\nblocked_u\n\n")
    _use_blocklist(monkeypatch, str(bl))
    c = AgentAdmissionController(
        max_users=None, max_loops_per_user=None, max_loops_global=None, min_free_mem_mb=0
    )
    with pytest.raises(adm.AccessDeniedError):
        await c.acquire("blocked_u")
    # non-blocked user is admitted normally
    tok = await c.acquire("ok_u")
    await c.release(tok)


def test_local_default_blocks_nobody(monkeypatch):
    # No file configured (the local/desktop default) → is_user_blocked is False
    # for everyone, so the gate is a no-op (binding rule #7).
    _use_blocklist(monkeypatch, None)
    assert adm.is_user_blocked("anyone") is False


def test_blocklist_hot_reloads_by_mtime(tmp_path, monkeypatch):
    bl = tmp_path / "users.txt"
    bl.write_text("u1\n")
    _use_blocklist(monkeypatch, str(bl))
    assert adm.is_user_blocked("u1") is True
    assert adm.is_user_blocked("u2") is False
    # rewrite + bump mtime → the set reloads
    bl.write_text("u2\n")
    future = time.time() + 10
    os.utime(str(bl), (future, future))
    assert adm.is_user_blocked("u2") is True
    assert adm.is_user_blocked("u1") is False


def test_absent_file_fails_open(tmp_path, monkeypatch):
    # Configured path that doesn't exist → keep last-known (empty) set, no crash.
    _use_blocklist(monkeypatch, str(tmp_path / "nope.txt"))
    assert adm.is_user_blocked("whoever") is False
