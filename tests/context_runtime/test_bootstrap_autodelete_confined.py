"""
@file_name: test_bootstrap_autodelete_confined.py
@author: Bin Liang
@date: 2026-10-30
@description: The Bootstrap.md auto-delete in ContextRuntime._settle_bootstrap
goes through the link-free walk on the lexical path: a tenant that swaps its
agent dir for a link into another tenant's tree after the presence check must
not get the victim's Bootstrap.md deleted.
"""
from __future__ import annotations

import os
from types import SimpleNamespace

import pytest

from narranexus.platform.context_runtime import context_runtime as cr


@pytest.fixture
def base(tmp_path, monkeypatch):
    from narranexus.platform.settings import settings

    b = tmp_path / "ws"
    b.mkdir()
    monkeypatch.setattr(settings, "base_working_path", str(b))
    return b


def _runtime_and_status(monkeypatch, bootstrap_path):
    status = SimpleNamespace(present=True, active=False, event_count=9, threshold=3,
                             bootstrap_path=str(bootstrap_path))

    async def fake_active(*_a, **_k):
        return status

    class Repo:
        def __init__(self, db):
            pass

        async def get_agent(self, agent_id):
            return SimpleNamespace(created_by="u1", agent_metadata={})

    monkeypatch.setattr("narranexus.platform.bootstrap.lifecycle.is_bootstrap_active", fake_active)
    monkeypatch.setattr("narranexus.platform.repository.AgentRepository", Repo)
    return SimpleNamespace(db=object(), agent_id="agent_a")


async def test_honest_auto_delete_removes_the_owners_file(base, monkeypatch):
    ws = base / "u1" / "agent_a"
    ws.mkdir(parents=True)
    (ws / "Bootstrap.md").write_text("hi", encoding="utf-8")
    rt = _runtime_and_status(monkeypatch, ws / "Bootstrap.md")
    await cr.ContextRuntime._settle_bootstrap(rt, SimpleNamespace(), "u1")
    assert not (ws / "Bootstrap.md").exists()


async def test_swapped_agent_dir_never_deletes_the_victims_file(base, monkeypatch):
    victim = base / "u2" / "agent_v"
    victim.mkdir(parents=True)
    (victim / "Bootstrap.md").write_text("victim", encoding="utf-8")
    (base / "u1").mkdir()
    os.symlink(victim, base / "u1" / "agent_a")   # swapped after the presence check
    rt = _runtime_and_status(monkeypatch, base / "u1" / "agent_a" / "Bootstrap.md")
    await cr.ContextRuntime._settle_bootstrap(rt, SimpleNamespace(), "u1")
    assert (victim / "Bootstrap.md").read_text(encoding="utf-8") == "victim"
