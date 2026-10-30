"""
@file_name: test_tenant_tree_sites_confined.py
@author: Bin Liang
@date: 2026-10-30
@description: Delete and listing sites inside a tenant's own mount used to
walk paths BY STRING. ``workspace/skills`` (and ``_shared/teams``, the agent
dir, any subdirectory) are tenant-replaceable, so a swapped directory made a
by-path ``rmtree`` delete another tenant's skill / team folder, and a by-path
listing / stat disclose another tenant's names, sizes and mtimes.

Every case plants (or races in) a link to a "victim" tree and asserts the
victim is byte-for-byte untouched and never listed. They go red when the
site is reverted to its by-path implementation.
"""
from __future__ import annotations

import json
import os

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from narranexus.platform.settings import settings
from narranexus.platform.utils.workspace_paths import agent_workspace_relpath

OWNER, VICTIM = "u1", "u2"
A_OWN, A_VIC = "agent_0123456789ab", "agent_ba9876543210"


def _link(path, target):
    os.symlink(target, path)


def _swap_dir_for_link(directory, victim_dir):
    os.rename(directory, str(directory) + ".moved")
    os.symlink(victim_dir, directory)


@pytest.fixture
def ws(tmp_path, monkeypatch):
    base = tmp_path / "ws"
    monkeypatch.setattr(settings, "base_working_path", str(base))
    own = base / agent_workspace_relpath(A_OWN, OWNER)
    vic = base / agent_workspace_relpath(A_VIC, VICTIM)
    own.mkdir(parents=True)
    vic.mkdir(parents=True)
    return {"base": base, "own": own, "vic": vic}


def _skill_md(name):
    return f"---\nname: {name}\ndescription: d\n---\n\n# {name}\n"


def _victim_skill(ws):
    d = ws["vic"] / "skills" / "demo"
    d.mkdir(parents=True)
    (d / "SKILL.md").write_text(_skill_md("demo"), encoding="utf-8")
    (d / "data.bin").write_bytes(b"victim-data")
    return d


# ── skill_module.remove_skill / install_from_dir ─────────────────────────────


def _module():
    from narranexus_plugins.skill_module.skill_module import SkillModule

    return SkillModule(agent_id=A_OWN, user_id=OWNER)


def test_remove_skill_deletes_the_owners_skill_and_disabled_copy(ws):
    sm = _module()
    for p in (ws["own"] / "skills" / "mine", ws["own"] / "skills" / ".disabled" / "off"):
        p.mkdir(parents=True)
        (p / "SKILL.md").write_text(_skill_md("x"), encoding="utf-8")
    assert sm.remove_skill("mine") is True
    assert sm.remove_skill("off") is True
    assert sm.remove_skill("ghost") is False
    assert not (ws["own"] / "skills" / "mine").exists()
    assert not (ws["own"] / "skills" / ".disabled" / "off").exists()


def test_remove_skill_refuses_a_swapped_skills_dir_and_spares_the_victim(ws):
    victim = _victim_skill(ws)
    _link(ws["own"] / "skills", ws["vic"] / "skills")
    sm = _module()
    with pytest.raises(ValueError, match="refusing"):
        sm.remove_skill("demo")
    assert (victim / "data.bin").read_bytes() == b"victim-data"


def test_remove_skill_never_follows_a_link_inside_the_skill(ws):
    victim = _victim_skill(ws)
    mine = ws["own"] / "skills" / "mine"
    mine.mkdir(parents=True)
    (mine / "SKILL.md").write_text(_skill_md("mine"), encoding="utf-8")
    _link(mine / "lnk", victim)
    assert _module().remove_skill("mine") is True
    assert not mine.exists()
    assert (victim / "data.bin").read_bytes() == b"victim-data"


def test_install_from_dir_replaces_an_existing_skill(ws, tmp_path):
    sm = _module()
    old = ws["own"] / "skills" / "demo"
    old.mkdir(parents=True)
    (old / "SKILL.md").write_text(_skill_md("demo"), encoding="utf-8")
    (old / "stale.txt").write_text("stale", encoding="utf-8")
    staged = tmp_path / "staged"
    staged.mkdir()
    (staged / "SKILL.md").write_text(_skill_md("demo"), encoding="utf-8")
    sm.install_from_dir(staged, source_type="zip")
    assert (old / "SKILL.md").exists()
    assert not (old / "stale.txt").exists()


def test_install_from_dir_refuses_a_swapped_skills_dir_and_spares_the_victim(ws, tmp_path):
    victim = _victim_skill(ws)
    _link(ws["own"] / "skills", ws["vic"] / "skills")
    staged = tmp_path / "staged"
    staged.mkdir()
    (staged / "SKILL.md").write_text(_skill_md("demo"), encoding="utf-8")
    with pytest.raises(ValueError, match="refusing"):
        _module().install_from_dir(staged, source_type="zip")
    assert (victim / "data.bin").read_bytes() == b"victim-data"
    assert sorted(os.listdir(victim)) == ["SKILL.md", "data.bin"]


# ── bundle importer.copy_skill_into_agent ────────────────────────────────────


def _src_skill(tmp_path):
    src = tmp_path / "src_skill"
    src.mkdir()
    (src / "SKILL.md").write_text(_skill_md("demo"), encoding="utf-8")
    return src


def test_copy_skill_into_agent_replaces_an_existing_skill(ws, tmp_path):
    from narranexus.platform.bundle import importer

    old = ws["own"] / "skills" / "demo"
    old.mkdir(parents=True)
    (old / "stale.txt").write_text("stale", encoding="utf-8")
    importer.copy_skill_into_agent(_src_skill(tmp_path), A_OWN, OWNER, "demo", base=str(ws["base"]))
    assert (old / "SKILL.md").exists()
    assert not (old / "stale.txt").exists()


def test_copy_skill_into_agent_refuses_a_swapped_skills_dir_and_spares_the_victim(ws, tmp_path):
    from narranexus.platform.bundle import importer

    victim = _victim_skill(ws)
    _link(ws["own"] / "skills", ws["vic"] / "skills")
    with pytest.raises(ValueError):
        importer.copy_skill_into_agent(_src_skill(tmp_path), A_OWN, OWNER, "demo", base=str(ws["base"]))
    assert (victim / "data.bin").read_bytes() == b"victim-data"
    assert sorted(os.listdir(victim)) == ["SKILL.md", "data.bin"]


# ── agents/files.py: directory tree listing ──────────────────────────────────


async def _async_return(value):
    return value


@pytest.fixture
async def files_client(db_client, ws, monkeypatch):
    import backend.routes._ownership as own_mod
    import backend.routes.agents.files as files_mod

    monkeypatch.setattr(own_mod, "get_db_client", lambda: _async_return(db_client))
    await db_client.insert("agents", {"agent_id": A_OWN, "agent_name": "mine", "created_by": OWNER})
    app = FastAPI()

    @app.middleware("http")
    async def _identity(request: Request, call_next):
        request.state.user_id = request.headers.get("X-User-Id") or None
        return await call_next(request)

    app.include_router(files_mod.router, prefix="/api/agents")
    return {**ws, "client": TestClient(app), "files_mod": files_mod}


def _tree_names(nodes):
    out = []
    for n in nodes:
        out.append(n["name"])
        out.extend(_tree_names(n.get("children") or []))
    return out


def test_tree_lists_the_owners_nested_files_with_sizes(files_client):
    (files_client["own"] / "report" / "sub").mkdir(parents=True)
    (files_client["own"] / "report" / "a.txt").write_bytes(b"12345")
    (files_client["own"] / "report" / "sub" / "b.txt").write_bytes(b"1")
    (files_client["own"] / ".hidden").write_bytes(b"x")
    r = files_client["client"].get(f"/api/agents/{A_OWN}/files", headers={"X-User-Id": OWNER})
    body = r.json()
    assert body["success"] is True
    assert sorted(_tree_names(body["tree"])) == ["a.txt", "b.txt", "report", "sub"]
    report = body["tree"][0]
    assert report["is_dir"] is True
    assert {c["name"]: c["size"] for c in report["children"]}["a.txt"] == 5


def test_tree_for_an_empty_missing_workspace_is_empty_not_an_error(files_client):
    import shutil

    shutil.rmtree(files_client["own"])
    r = files_client["client"].get(f"/api/agents/{A_OWN}/files", headers={"X-User-Id": OWNER})
    assert r.json()["success"] is True
    assert r.json()["tree"] == []


def test_tree_swap_between_listing_and_recursion_never_lists_the_victim(files_client, monkeypatch):
    """The walk lists a directory, and only THEN recurses into its children. A
    child swapped for a link in that gap used to be scandir()-ed by path."""
    (files_client["own"] / "report").mkdir()
    (files_client["own"] / "report" / "a.txt").write_bytes(b"x")
    (files_client["vic"] / "secret").mkdir()
    (files_client["vic"] / "secret" / "victim-only-name.txt").write_bytes(b"v" * 77)
    real = os.scandir
    fired = []

    class _Listed:
        """A finished scan: the entries are read BEFORE the swap (readdir would
        otherwise reflect the rename), then handed out as a normal scandir."""

        def __init__(self, inner):
            self._inner = inner
            self._entries = list(inner)

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def __iter__(self):
            return iter(self._entries)

        def close(self):
            self._inner.close()

    def swapping(*a, **k):
        out = real(*a, **k)
        # Fire right after the workspace root itself was listed (by path in the
        # old walk, by dir fd in the confined one) — not on unrelated scandirs.
        arg = a[0] if a else k.get("path")
        listed_root = isinstance(arg, int) or (
            isinstance(arg, (str, os.PathLike)) and os.fspath(arg) == str(files_client["own"])
        )
        if listed_root and not fired:
            fired.append(True)
            listed = _Listed(out)
            _swap_dir_for_link(files_client["own"] / "report", files_client["vic"] / "secret")
            return listed
        return out

    monkeypatch.setattr(os, "scandir", swapping)
    # the confined walk checks that scandir supports fd arguments (fail closed)
    monkeypatch.setattr(os, "supports_fd", os.supports_fd | {swapping})
    r = files_client["client"].get(f"/api/agents/{A_OWN}/files", headers={"X-User-Id": OWNER})
    assert fired, "the swap must actually have happened"
    assert "victim-only-name.txt" not in r.text
    assert "77" not in [str(n.get("size")) for n in r.json().get("tree", [])]


# ── manyfold list / stat ─────────────────────────────────────────────────────


@pytest.fixture
def manyfold(ws, monkeypatch):
    import backend.routes.manyfold.files as mf

    async def fake_root(agent_id):
        return ws["own"], OWNER

    monkeypatch.setattr(mf, "_resolve_workspace_root", fake_root)
    app = FastAPI()

    @app.middleware("http")
    async def _authed(request: Request, call_next):
        request.state.manyfold_authed = True
        return await call_next(request)

    app.include_router(mf.router)
    return {**ws, "mf": mf, "client": TestClient(app)}


def _list(m, path=""):
    return m["client"].get(f"/manyfold/agents/{A_OWN}/files/list", params={"path": path})


def _stat(m, path):
    return m["client"].get(f"/manyfold/agents/{A_OWN}/files/stat", params={"path": path})


def test_manyfold_list_and_stat_report_the_owners_entries(manyfold):
    (manyfold["own"] / "report").mkdir()
    (manyfold["own"] / "report" / "a.txt").write_bytes(b"12345")
    r = _list(manyfold, "report")
    assert r.status_code == 200
    assert [(e["name"], e["type"], e["size"]) for e in r.json()["entries"]] == [("a.txt", "file", 5)]
    top = _list(manyfold).json()["entries"]
    assert [(e["name"], e["type"]) for e in top] == [("report", "dir")]
    s = _stat(manyfold, "report/a.txt").json()["entry"]
    assert (s["name"], s["type"], s["size"]) == ("a.txt", "file", 5)
    assert _stat(manyfold, "report").json()["entry"]["type"] == "dir"


def test_manyfold_list_missing_dir_and_file_dir_errors(manyfold):
    (manyfold["own"] / "report").mkdir()
    (manyfold["own"] / "report" / "a.txt").write_bytes(b"1")
    assert _list(manyfold, "nope").status_code == 404
    assert _list(manyfold, "report/a.txt").status_code == 400
    assert _stat(manyfold, "nope").status_code == 404


def test_manyfold_list_of_a_missing_workspace_is_empty(manyfold):
    import shutil

    shutil.rmtree(manyfold["own"])
    r = _list(manyfold)
    assert r.status_code == 200
    assert r.json() == {"entries": []}


def test_manyfold_list_reports_a_planted_link_as_a_link_with_its_own_metadata(manyfold):
    """child.stat() followed the link: the victim file's size/mtime leaked."""
    (manyfold["own"] / "report").mkdir()
    victim_file = manyfold["vic"] / "secret.bin"
    victim_file.write_bytes(b"v" * 4321)
    _link(manyfold["own"] / "report" / "lnk", victim_file)
    entries = _list(manyfold, "report").json()["entries"]
    assert [(e["name"], e["type"]) for e in entries] == [("lnk", "link")]
    assert entries[0]["size"] != 4321


def test_manyfold_list_swap_after_resolve_never_lists_the_victim(manyfold, monkeypatch):
    (manyfold["own"] / "report").mkdir()
    (manyfold["vic"] / "secret").mkdir()
    (manyfold["vic"] / "secret" / "victim-only-name.txt").write_bytes(b"v")
    mf = manyfold["mf"]
    real = mf._safe_resolve

    def swapping(workspace, raw):
        out = real(workspace, raw)
        _swap_dir_for_link(manyfold["own"] / "report", manyfold["vic"] / "secret")
        return out

    monkeypatch.setattr(mf, "_safe_resolve", swapping)
    r = _list(manyfold, "report")
    assert r.status_code == 404
    assert "victim-only-name.txt" not in r.text


def test_manyfold_stat_swap_after_resolve_never_reports_the_victim(manyfold, monkeypatch):
    (manyfold["own"] / "report").mkdir()
    (manyfold["own"] / "report" / "a.txt").write_bytes(b"1")
    (manyfold["vic"] / "secret").mkdir()
    (manyfold["vic"] / "secret" / "a.txt").write_bytes(b"v" * 999)
    mf = manyfold["mf"]
    real = mf._safe_resolve

    def swapping(workspace, raw):
        out = real(workspace, raw)
        _swap_dir_for_link(manyfold["own"] / "report", manyfold["vic"] / "secret")
        return out

    monkeypatch.setattr(mf, "_safe_resolve", swapping)
    r = _stat(manyfold, "report/a.txt")
    assert r.status_code == 404
    assert "999" not in r.text


# ── team shared-folder wipe ──────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_team_wipe_never_deletes_through_a_swapped_shared_dir(db_client, ws):
    from narranexus_plugins.teams.routes import _wipe_team_data
    from narranexus.platform.schema.team_schema import Team
    from narranexus.platform.utils.workspace_paths import team_shared_dir

    base = str(ws["base"])
    tid = "team_abc"
    mine = team_shared_dir("u_own", tid, base=base)
    mine.mkdir(parents=True)
    (mine / "plan.md").write_text("mine", encoding="utf-8")
    theirs = team_shared_dir("u_vic", tid, base=base)  # same team id in the victim's tree
    theirs.mkdir(parents=True)
    (theirs / "secret.md").write_text("victim", encoding="utf-8")
    shared = ws["base"] / "u_own" / "_shared"
    os.rename(shared, str(shared) + ".moved")
    _link(shared, ws["base"] / "u_vic" / "_shared")

    team = Team(team_id=tid, owner_user_id="u_own", name="T")
    res = await _wipe_team_data(db_client, team, clear_chat=False, clear_files=True)

    assert res["files_removed"] is False
    assert (theirs / "secret.md").read_text(encoding="utf-8") == "victim"


# ── agent delete: workspace removal ──────────────────────────────────────────


@pytest.fixture
async def delete_client(db_client, ws, monkeypatch):
    import backend.routes.auth as auth_mod
    import narranexus.platform.utils.db.db_factory as db_factory_mod

    async def _get_db():
        return db_client

    monkeypatch.setattr(db_factory_mod, "get_db_client", _get_db)
    monkeypatch.setattr(auth_mod, "get_db_client", _get_db)
    await db_client.insert("agents", {"agent_id": A_OWN, "agent_name": "A", "created_by": OWNER})
    app = FastAPI()
    app.include_router(auth_mod.router, prefix="/api/auth")

    @app.middleware("http")
    async def _fake_auth(request, call_next):
        request.state.user_id = OWNER
        return await call_next(request)

    return {**ws, "client": TestClient(app)}


async def test_agent_delete_removes_the_workspace_tree(delete_client):
    (delete_client["own"] / "report").mkdir()
    (delete_client["own"] / "report" / "a.txt").write_bytes(b"x")
    r = delete_client["client"].delete(f"/api/auth/agents/{A_OWN}")
    assert r.json()["success"] is True
    assert not delete_client["own"].exists()


async def test_agent_delete_with_a_swapped_workspace_unlinks_the_link_and_spares_the_victim(delete_client):
    """A by-path rmtree refuses a symlink root and leaves the planted link
    behind; the confined removal unlinks the LINK and never touches its target."""
    (delete_client["vic"] / "report").mkdir()
    (delete_client["vic"] / "report" / "a.txt").write_bytes(b"victim")
    import shutil

    shutil.rmtree(delete_client["own"])
    _link(delete_client["own"], delete_client["vic"])
    r = delete_client["client"].delete(f"/api/auth/agents/{A_OWN}")
    assert r.json()["success"] is True
    assert not os.path.lexists(delete_client["own"])
    assert (delete_client["vic"] / "report" / "a.txt").read_bytes() == b"victim"


# ── skill disable / enable / install / materialize / metadata ────────────────


def _owner_skill(ws, name="demo", where="skills"):
    d = ws["own"] / where / name
    d.mkdir(parents=True)
    (d / "SKILL.md").write_text(_skill_md(name), encoding="utf-8")
    return d


def test_disable_and_enable_move_the_owners_skill(ws):
    sm = _module()
    d = _owner_skill(ws)
    assert sm.disable_skill("demo") is True
    assert not d.exists()
    assert (ws["own"] / "skills" / ".disabled" / "demo" / "SKILL.md").exists()
    assert sm.disable_skill("demo") is False
    assert sm.enable_skill("demo") is True
    assert (ws["own"] / "skills" / "demo" / "SKILL.md").exists()
    assert sm.enable_skill("demo") is False


def test_disable_refuses_a_swapped_skills_dir_and_moves_nothing_in_the_victim(ws):
    victim = _victim_skill(ws)
    _link(ws["own"] / "skills", ws["vic"] / "skills")
    with pytest.raises(ValueError, match="refusing"):
        _module().disable_skill("demo")
    assert (victim / "data.bin").read_bytes() == b"victim-data"
    assert not (ws["vic"] / "skills" / ".disabled").exists()


def test_enable_refuses_a_swapped_disabled_dir_and_steals_nothing(ws):
    vdis = ws["vic"] / "skills" / ".disabled" / "demo"
    vdis.mkdir(parents=True)
    (vdis / "data.bin").write_bytes(b"victim-data")
    (ws["own"] / "skills").mkdir(parents=True)
    _link(ws["own"] / "skills" / ".disabled", ws["vic"] / "skills" / ".disabled")
    with pytest.raises(ValueError, match="refusing"):
        _module().enable_skill("demo")
    assert (vdis / "data.bin").read_bytes() == b"victim-data"
    assert not (ws["own"] / "skills" / "demo").exists()


def test_install_swap_between_removal_and_copy_never_writes_into_the_victim(ws, tmp_path, monkeypatch):
    from narranexus_plugins.skill_module.skill_module import SkillModule

    victim = _victim_skill(ws)
    (ws["own"] / "skills").mkdir(parents=True)
    staged = tmp_path / "staged"
    staged.mkdir()
    (staged / "SKILL.md").write_text(_skill_md("other"), encoding="utf-8")
    real = SkillModule._remove_skill_dir

    def swapping(self, path):
        out = real(self, path)
        _swap_dir_for_link(ws["own"] / "skills", ws["vic"] / "skills")
        return out

    monkeypatch.setattr(SkillModule, "_remove_skill_dir", swapping)
    before = sorted(os.listdir(ws["vic"] / "skills"))
    with pytest.raises(ValueError, match="refusing"):
        _module().install_from_dir(staged, source_type="zip")
    assert sorted(os.listdir(ws["vic"] / "skills")) == before
    assert sorted(os.listdir(victim)) == ["SKILL.md", "data.bin"]


def test_install_writes_provenance_meta_and_leaves_no_staging_droppings(ws, tmp_path):
    staged = tmp_path / "staged"
    staged.mkdir()
    (staged / "SKILL.md").write_text(_skill_md("demo"), encoding="utf-8")
    (staged / "run.sh").write_text("#!/bin/sh\n", encoding="utf-8")
    os.chmod(staged / "run.sh", 0o755)
    _module().install_from_dir(staged, source_type="github", source_url="https://example.org/r")
    d = ws["own"] / "skills" / "demo"
    assert sorted(os.listdir(ws["own"] / "skills")) == ["demo"]
    meta = json.loads((d / ".skill_meta.json").read_text(encoding="utf-8"))
    assert meta["source_type"] == "github" and meta["source_url"] == "https://example.org/r"
    assert os.stat(d / "run.sh").st_mode & 0o777 == 0o755


def test_install_creates_a_missing_workspace_base(tmp_path, monkeypatch):
    base = tmp_path / "fresh" / "ws"  # nothing exists yet (first run)
    monkeypatch.setattr(settings, "base_working_path", str(base))
    staged = tmp_path / "staged"
    staged.mkdir()
    (staged / "SKILL.md").write_text(_skill_md("demo"), encoding="utf-8")
    _module().install_from_dir(staged, source_type="zip")
    assert (base / agent_workspace_relpath(A_OWN, OWNER) / "skills" / "demo" / "SKILL.md").exists()


def test_materialize_builtin_never_writes_into_a_swapped_skills_dir(ws, tmp_path, monkeypatch):
    from narranexus_plugins.skill_module import skill_module as sm_mod

    src_root = tmp_path / "builtin_src"
    (src_root / "tool").mkdir(parents=True)
    (src_root / "tool" / "SKILL.md").write_text(_skill_md("tool"), encoding="utf-8")
    monkeypatch.setattr(sm_mod, "BUILTIN_SKILLS_DIR", src_root)
    (ws["vic"] / "skills").mkdir(parents=True)
    _link(ws["own"] / "skills", ws["vic"] / "skills")
    _module()._materialize_builtin_skills()
    assert os.listdir(ws["vic"] / "skills") == []


def test_materialize_builtin_still_materializes_for_the_owner(ws, tmp_path, monkeypatch):
    from narranexus_plugins.skill_module import skill_module as sm_mod

    src_root = tmp_path / "builtin_src"
    (src_root / "tool").mkdir(parents=True)
    (src_root / "tool" / "SKILL.md").write_text(_skill_md("tool"), encoding="utf-8")
    monkeypatch.setattr(sm_mod, "BUILTIN_SKILLS_DIR", src_root)
    _module()._materialize_builtin_skills()
    meta = json.loads((ws["own"] / "skills" / "tool" / ".skill_meta.json").read_text(encoding="utf-8"))
    assert meta["builtin"] is True
    assert sorted(os.listdir(ws["own"] / "skills")) == ["tool"]


def test_study_status_write_never_lands_in_a_swapped_skill_dir(ws):
    victim = _victim_skill(ws)
    (victim / ".skill_meta.json").write_text('{"keep": "victim"}', encoding="utf-8")
    (ws["own"] / "skills").mkdir(parents=True)
    _link(ws["own"] / "skills" / "demo", victim)
    sm = _module()
    sm.set_study_status("demo", "completed", result="pwn")
    assert json.loads((victim / ".skill_meta.json").read_text(encoding="utf-8")) == {"keep": "victim"}
    assert sm.get_study_status("demo") == {"study_status": "idle"}


def test_study_status_round_trips_for_the_owner(ws):
    _owner_skill(ws)
    sm = _module()
    sm.set_study_status("demo", "completed", result="ok")
    got = sm.get_study_status("demo")
    assert got["study_status"] == "completed" and got["study_result"] == "ok"


def test_copy_skill_into_agent_source_behind_a_swapped_link_is_refused(ws, tmp_path):
    from narranexus.platform.bundle import importer
    from narranexus.platform.utils import confined_io

    victim = _victim_skill(ws)
    _link(ws["own"] / "skills", ws["vic"] / "skills")  # the SOURCE walks through this
    other = "agent_aaaaaaaaaaaa"
    with pytest.raises(confined_io.ConfinedPathError):
        importer.copy_skill_into_agent(
            ws["own"] / "skills" / "demo", other, OWNER, "demo", base=str(ws["base"])
        )
    assert not (ws["base"] / agent_workspace_relpath(other, OWNER) / "skills" / "demo").exists()
    assert (victim / "data.bin").read_bytes() == b"victim-data"


def test_copy_skill_into_agent_copies_the_owners_installed_skill(ws):
    from narranexus.platform.bundle import importer

    src = _owner_skill(ws)
    (src / "run.sh").write_text("#!/bin/sh\n", encoding="utf-8")
    other = "agent_aaaaaaaaaaaa"
    target = importer.copy_skill_into_agent(src, other, OWNER, "demo", base=str(ws["base"]))
    assert (target / "SKILL.md").read_text(encoding="utf-8") == _skill_md("demo")
    assert (target / "run.sh").exists()


def test_skill_scan_and_resolution_never_surface_a_swapped_skills_dir(ws):
    """Listing / reading SKILL.md by path showed the victim's skills (names and
    descriptions) in the owner's skill table."""
    _victim_skill(ws)
    _link(ws["own"] / "skills", ws["vic"] / "skills")
    sm = _module()
    assert "demo" not in [s.name for s in sm._scan_skills()]
    assert sm._resolve_skill_dir("demo") is None
    assert sm._read_skill_meta("demo") == {}


def test_skill_scan_lists_the_owners_skills_and_disabled_ones(ws):
    _owner_skill(ws, "alpha")
    _owner_skill(ws, "beta", where="skills/.disabled")
    sm = _module()
    assert [s.name for s in sm._scan_skills() if s.source_type != "plugin"] == ["alpha"]
    listed = {
        s.name: s.disabled
        for s in sm.list_skills(include_disabled=True)
        if s.source_type != "plugin" and not s.builtin  # list_skills materializes the vendored built-ins
    }
    assert listed == {"alpha": False, "beta": True}
    assert sm._resolve_skill_dir("alpha") == ws["own"] / "skills" / "alpha"


def test_install_meta_write_uses_the_lexical_skill_path_not_a_resolved_one(ws, tmp_path, monkeypatch):
    """ensure_within_directory resolve()s: with a `skills` link in place at that
    instant it returns the VICTIM's real skill dir. If the link is swapped back
    before the rename, a meta write through that value would merge and rewrite
    the victim's env_config."""
    from narranexus_plugins.skill_module import skill_module as sm_mod

    victim = _victim_skill(ws)
    victim_meta = victim / ".skill_meta.json"
    victim_meta.write_text('{"env_config": {"API_KEY": "victim-secret"}}', encoding="utf-8")
    (ws["own"] / "skills").mkdir(parents=True)
    monkeypatch.setattr(sm_mod, "ensure_within_directory", lambda base, name, label="x": victim)
    staged = tmp_path / "staged"
    staged.mkdir()
    (staged / "SKILL.md").write_text(_skill_md("demo"), encoding="utf-8")
    info = _module().install_from_dir(staged, source_type="zip")
    assert json.loads(victim_meta.read_text(encoding="utf-8")) == {"env_config": {"API_KEY": "victim-secret"}}
    own_meta = json.loads((ws["own"] / "skills" / "demo" / ".skill_meta.json").read_text(encoding="utf-8"))
    assert own_meta["source_type"] == "zip"
    assert info.path == str(ws["own"] / "skills" / "demo")
