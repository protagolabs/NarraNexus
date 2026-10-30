"""
@file_name: test_provisioning_confined.py
@author: Bin Liang
@date: 2026-10-30
@description: Platform-side provisioning / import / outbound-media code touched
the agent's tenant-writable workspace BY PATH: Bootstrap.md write, the welcome
artifact, the CLI CWD mkdir, the migration skill copy, Matrix media reads, the
managed-ingress attachment read and the Arena credentials read. A directory
swapped for (or a file planted as) a link into another tenant's tree must make
each of them refuse, never write or read there.
"""
from __future__ import annotations

import os

import pytest

from narranexus.platform.settings import settings
from narranexus.platform.utils.workspace_paths import agent_workspace_path, agent_workspace_relpath

AGENT, USER = "agent_0123456789ab", "u1"
V_AGENT, V_USER = "agent_ba9876543210", "u2"
SECRET = b"victim-secret-bytes"


@pytest.fixture
def ws(tmp_path, monkeypatch):
    base = tmp_path / "ws"
    monkeypatch.setattr(settings, "base_working_path", str(base), raising=False)
    own = base / agent_workspace_relpath(AGENT, USER)
    vic = base / agent_workspace_relpath(V_AGENT, V_USER)
    own.mkdir(parents=True)
    vic.mkdir(parents=True)
    return {"base": base, "own": own, "vic": vic, "tmp": tmp_path}


# ── bootstrap profile writes ─────────────────────────────────────────────────


class _Profile:
    name = "p"
    auto_delete_after_events = 0

    def __init__(self, md="# hello", welcome=None):
        self._md, self._welcome = md, welcome

    def bootstrap_md(self, ctx):
        return self._md

    def greeting(self, ctx):
        return "hi"

    def welcome_artifact(self, ctx):
        return self._welcome


class _FakeRepo:
    def __init__(self, db):
        pass

    async def get_agent(self, agent_id):
        return None

    async def update_agent(self, agent_id, upd):
        pass


@pytest.mark.asyncio
async def test_bootstrap_md_is_written_and_removed_through_the_confined_walk(ws, monkeypatch):
    import narranexus.platform.repository.agent_repository as repo_mod
    from narranexus.platform.bootstrap.profiles import apply_bootstrap

    monkeypatch.setattr(repo_mod, "AgentRepository", _FakeRepo)
    await apply_bootstrap(None, agent_id=AGENT, user_id=USER, profile=_Profile("# hello"))
    assert (ws["own"] / "Bootstrap.md").read_text(encoding="utf-8") == "# hello"
    await apply_bootstrap(None, agent_id=AGENT, user_id=USER, profile=_Profile(md=""))
    assert not (ws["own"] / "Bootstrap.md").exists()


@pytest.mark.asyncio
async def test_bootstrap_md_link_planted_at_the_name_is_not_written_through(ws, monkeypatch):
    import narranexus.platform.repository.agent_repository as repo_mod
    from narranexus.platform.bootstrap.profiles import apply_bootstrap

    monkeypatch.setattr(repo_mod, "AgentRepository", _FakeRepo)
    victim_file = ws["vic"] / "precious.md"
    victim_file.write_bytes(SECRET)
    os.symlink(victim_file, ws["own"] / "Bootstrap.md")
    await apply_bootstrap(None, agent_id=AGENT, user_id=USER, profile=_Profile("# mine"))
    assert victim_file.read_bytes() == SECRET


@pytest.mark.asyncio
async def test_bootstrap_into_a_swapped_workspace_refuses_and_writes_nothing_in_the_victim(ws, monkeypatch):
    import shutil

    import narranexus.platform.repository.agent_repository as repo_mod
    from narranexus.platform.bootstrap.profiles import apply_bootstrap

    monkeypatch.setattr(repo_mod, "AgentRepository", _FakeRepo)
    shutil.rmtree(ws["own"])
    os.symlink(ws["vic"], ws["own"])
    with pytest.raises(ValueError):
        await apply_bootstrap(None, agent_id=AGENT, user_id=USER, profile=_Profile("# mine"))
    assert os.listdir(ws["vic"]) == []


# ── CLI working directory ────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_cli_cwd_is_created_for_the_owner_and_refused_for_a_swapped_workspace(ws, monkeypatch):
    import shutil

    from narranexus.platform.module_system.data_access import workspace_cwd as cwd_mod

    class _Store:
        async def get_agent_owner(self, agent_id):
            return USER

    monkeypatch.setattr(cwd_mod, "get_channel_credential_store", lambda: _Store())
    cwd_mod._cwd_owner_cache.clear()
    got = await cwd_mod.resolve_agent_workspace_cwd(AGENT, log_tag="t")
    assert got == ws["own"]
    shutil.rmtree(ws["own"])
    os.symlink(ws["vic"], ws["own"])
    assert await cwd_mod.resolve_agent_workspace_cwd(AGENT, log_tag="t") is None
    assert os.listdir(ws["vic"]) == []


# ── migration skill copy ─────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_migration_skill_copy_replaces_for_the_owner_and_refuses_a_swapped_skills_dir(ws):
    from narranexus.platform.migration.applier import _copy_local_skill

    src = ws["tmp"] / "local_skill"
    src.mkdir()
    (src / "SKILL.md").write_text("# s", encoding="utf-8")
    old = ws["own"] / "skills" / "demo"
    old.mkdir(parents=True)
    (old / "stale.txt").write_text("stale", encoding="utf-8")
    assert await _copy_local_skill(AGENT, USER, "demo", str(src)) is True
    assert sorted(os.listdir(old)) == ["SKILL.md"]

    # a `skills` link into the victim's tree: nothing is deleted or copied there
    vskills = ws["vic"] / "skills"
    (vskills / "demo").mkdir(parents=True)
    (vskills / "demo" / "keep.txt").write_bytes(SECRET)
    import shutil

    shutil.rmtree(ws["own"] / "skills")
    os.symlink(vskills, ws["own"] / "skills")
    with pytest.raises(ValueError):
        await _copy_local_skill(AGENT, USER, "demo", str(src))
    assert (vskills / "demo" / "keep.txt").read_bytes() == SECRET
    assert sorted(os.listdir(vskills / "demo")) == ["keep.txt"]


# ── Matrix outbound media ────────────────────────────────────────────────────


def test_matrix_resolve_returns_the_lexical_confined_path_and_refuses_a_swapped_dir(ws):
    from narranexus_plugins.narramessenger_module import _matrix_send as ms

    (ws["own"] / "out").mkdir()
    (ws["own"] / "out" / "pic.png").write_bytes(b"png")
    assert ms.resolve_workspace_file(AGENT, USER, "out/pic.png") == ws["own"] / "out" / "pic.png"
    (ws["vic"] / "secret").mkdir()
    (ws["vic"] / "secret" / "pic.png").write_bytes(SECRET)
    import shutil

    shutil.rmtree(ws["own"] / "out")
    os.symlink(ws["vic"] / "secret", ws["own"] / "out")
    with pytest.raises(ms.MatrixSendError):
        ms.resolve_workspace_file(AGENT, USER, "out/pic.png")


@pytest.mark.asyncio
async def test_matrix_send_swap_after_resolve_never_reads_the_victims_file(ws, monkeypatch):
    from narranexus_plugins.narramessenger_module import _matrix_send as ms

    (ws["own"] / "out").mkdir()
    (ws["own"] / "out" / "pic.png").write_bytes(b"png")
    (ws["vic"] / "secret").mkdir()
    (ws["vic"] / "secret" / "pic.png").write_bytes(SECRET)
    real = ms.resolve_workspace_file

    def swapping(agent_id, owner_id, file_path):
        out = real(agent_id, owner_id, file_path)
        os.rename(ws["own"] / "out", str(ws["own"] / "out") + ".moved")
        os.symlink(ws["vic"] / "secret", ws["own"] / "out")
        return out

    monkeypatch.setattr(ms, "resolve_workspace_file", swapping)
    res = await ms.send_media_impl(
        agent_id=AGENT, owner_id=USER, homeserver="http://h", token="t", room_id="!r:h",
        file_path="out/pic.png", max_bytes=10_000,
    )
    assert res["ok"] is False and res["error"] == "not_found"
    assert SECRET not in repr(res).encode()
