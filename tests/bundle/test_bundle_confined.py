"""
@file_name: test_bundle_confined.py
@author: Bin Liang
@date: 2026-10-30
@description: Export packing, skill-archive copy, the built-in skill scan, the
skill content hash and the reconciler walked / read tenant directories BY PATH
after an ``islink``-style check. The owner's sandbox can swap a directory for a
link into another tenant's tree at any moment, so a swap between a listing and
the read packed the victim's files into the owner's export, copied a victim's
zip into the archive registry, or recorded a victim's skills as the owner's.
All of it now walks fd-relative (``utils/confined_io``).

The ``*_swap_*`` cases list a directory, THEN swap it for a link, then let the
code read — they go red if the read is a path-string open again.
"""
from __future__ import annotations

import io
import json
import os
import tarfile
import zipfile
from pathlib import Path

import pytest

from narranexus.platform.settings import settings

SECRET = b"victim-secret-bytes"


def _swap_dir_for_link(directory: Path, victim_dir: Path) -> None:
    os.rename(directory, str(directory) + ".moved")
    os.symlink(victim_dir, directory)


class _Listed:
    """A finished scan: entries read BEFORE the swap (readdir would otherwise
    reflect the rename), handed out as a normal scandir."""

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


def _swap_after_listing(monkeypatch, directory: Path, victim_dir: Path):
    """Swap ``directory`` right after IT has been listed — by path (old walks)
    or as the n-th dir-fd listing (confined walks: root first, then the dir)."""
    real = os.scandir
    state = {"fd_calls": 0, "fired": False}

    def swapping(*a, **k):
        out = real(*a, **k)
        if state["fired"]:
            return out
        arg = a[0] if a else k.get("path")
        by_path = isinstance(arg, (str, os.PathLike)) and os.fspath(arg) == str(directory)
        by_fd = False
        if isinstance(arg, int):
            state["fd_calls"] += 1
            by_fd = state["fd_calls"] == 2  # 1 = the root, 2 = `directory`
        if by_path or by_fd:
            state["fired"] = True
            listed = _Listed(out)
            _swap_dir_for_link(directory, victim_dir)
            return listed
        return out

    monkeypatch.setattr(os, "scandir", swapping)
    monkeypatch.setattr(os, "supports_fd", os.supports_fd | {swapping})
    return state


@pytest.fixture
def trees(tmp_path, monkeypatch):
    base = tmp_path / "ws"
    monkeypatch.setattr(settings, "base_working_path", str(base), raising=False)
    own = base / "u1" / "agent_own"
    vic = base / "u2" / "agent_vic"
    (own / "work").mkdir(parents=True)
    (vic / "secret").mkdir(parents=True)
    (own / "work" / "a.txt").write_bytes(b"mine")
    (own / "top.txt").write_bytes(b"top")
    (vic / "secret" / "a.txt").write_bytes(SECRET)
    return {"base": base, "own": own, "vic": vic, "tmp": tmp_path}


def _tar_members(path: Path) -> dict:
    with tarfile.open(path, "r:gz") as tar:
        return {m.name: (tar.extractfile(m).read() if m.isfile() else None) for m in tar.getmembers()}


# ── workspace export ─────────────────────────────────────────────────────────


@pytest.mark.parametrize("kwargs", [{}, {"user_id": "u1"}], ids=["plain", "rewrite"])
def test_pack_workspace_includes_the_owners_files_and_dirs(trees, kwargs):
    from narranexus.platform.bundle import builder

    out = trees["tmp"] / "o.tar.gz"
    builder._pack_workspace_sync(trees["own"], out, set(), **kwargs)
    members = _tar_members(out)
    assert members["./work/a.txt"] == b"mine"
    assert members["./top.txt"] == b"top"
    assert "./work" in members and "." in members


def test_pack_workspace_rewrites_the_owner_id_and_honours_excludes(trees):
    from narranexus.platform.bundle import builder

    (trees["own"] / "note.md").write_text("owned by u1", encoding="utf-8")
    (trees["own"] / "skip.txt").write_bytes(b"x")
    out = trees["tmp"] / "o.tar.gz"
    builder._pack_workspace_sync(trees["own"], out, {"skip.txt"}, user_id="u1")
    members = _tar_members(out)
    assert members["./note.md"] == b"owned by <original_owner>"
    assert "./skip.txt" not in members


@pytest.mark.parametrize("kwargs", [{}, {"user_id": "u1"}], ids=["plain", "rewrite"])
def test_pack_workspace_swap_after_listing_never_packs_the_victim(trees, monkeypatch, kwargs):
    from narranexus.platform.bundle import builder

    state = _swap_after_listing(monkeypatch, trees["own"] / "work", trees["vic"] / "secret")
    out = trees["tmp"] / "o.tar.gz"
    builder._pack_workspace_sync(trees["own"], out, set(), **kwargs)
    assert state["fired"], "the swap must actually have happened"
    assert SECRET not in b"".join(v for v in _tar_members(out).values() if v)


def test_pack_workspace_never_follows_a_planted_link(trees):
    from narranexus.platform.bundle import builder

    os.symlink(trees["vic"] / "secret", trees["own"] / "lnk")
    os.symlink(trees["vic"] / "secret" / "a.txt", trees["own"] / "flnk")
    out = trees["tmp"] / "o.tar.gz"
    builder._pack_workspace_sync(trees["own"], out, set(), user_id="u1")
    members = _tar_members(out)
    assert not any(n.startswith("./lnk") or n == "./flnk" for n in members)
    assert SECRET not in b"".join(v for v in members.values() if v)


def test_builtin_skill_roots_never_come_from_a_swapped_skills_dir(trees):
    from narranexus.platform.bundle import builder

    vskill = trees["vic"] / "skills" / "officecli"
    vskill.mkdir(parents=True)
    (vskill / ".skill_meta.json").write_text('{"builtin": true}', encoding="utf-8")
    os.symlink(trees["vic"] / "skills", trees["own"] / "skills")
    assert builder._builtin_skill_relpaths(trees["own"]) == set()


def test_builtin_skill_roots_are_found_for_the_owner(trees):
    from narranexus.platform.bundle import builder

    d = trees["own"] / "skills" / "officecli"
    d.mkdir(parents=True)
    (d / ".skill_meta.json").write_text('{"builtin": true}', encoding="utf-8")
    (trees["own"] / "skills" / "mine").mkdir()
    assert builder._builtin_skill_relpaths(trees["own"]) == {"skills/officecli"}


# ── skill zip export ─────────────────────────────────────────────────────────


def _zip_names(path: Path) -> dict:
    with zipfile.ZipFile(path) as zf:
        return {n: zf.read(n) for n in zf.namelist()}


def test_zip_dir_packs_the_owners_skill(trees):
    from narranexus.platform.bundle import builder

    skill = trees["own"] / "skills" / "demo"
    (skill / "sub").mkdir(parents=True)
    (skill / "SKILL.md").write_bytes(b"# demo")
    (skill / "sub" / "x.bin").write_bytes(b"x" * 100)
    os.chmod(skill / "SKILL.md", 0o640)
    out = trees["tmp"] / "s.zip"
    builder._zip_dir(skill, out)
    assert _zip_names(out) == {"SKILL.md": b"# demo", "sub/x.bin": b"x" * 100}
    with zipfile.ZipFile(out) as zf:
        assert (zf.getinfo("SKILL.md").external_attr >> 16) & 0o777 == 0o640


def test_zip_dir_scrubs_secrets_when_asked(trees):
    from narranexus.platform.bundle import builder

    skill = trees["own"] / "skills" / "demo"
    skill.mkdir(parents=True)
    (skill / "SKILL.md").write_bytes(b"# demo")
    (skill / "credentials.json").write_bytes(b"{}")
    (skill / ".skill_meta.json").write_text(json.dumps({"env_config": {"K": "v"}, "x": 1}), encoding="utf-8")
    out = trees["tmp"] / "s.zip"
    builder._zip_dir(skill, out, scrub_secrets=True)
    names = _zip_names(out)
    assert "credentials.json" not in names
    assert json.loads(names[".skill_meta.json"])["env_config"] == {"K": ""} or "v" not in names[".skill_meta.json"].decode()


def test_zip_dir_swap_after_listing_never_packs_the_victim(trees, monkeypatch):
    from narranexus.platform.bundle import builder

    skill = trees["own"] / "skills" / "demo"
    (skill / "sub").mkdir(parents=True)
    (skill / "sub" / "a.txt").write_bytes(b"mine")
    (trees["vic"] / "secret").mkdir(exist_ok=True)
    state = _swap_after_listing(monkeypatch, skill / "sub", trees["vic"] / "secret")
    out = trees["tmp"] / "s.zip"
    builder._zip_dir(skill, out)
    assert state["fired"]
    assert SECRET not in b"".join(_zip_names(out).values())


# ── skill archive from a workspace zip ───────────────────────────────────────


def _skill_zip(path: Path, marker: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("SKILL.md", "---\nname: x\ndescription: d\n---\n")
        z.writestr("marker.txt", marker)


@pytest.mark.asyncio
async def test_archive_local_zip_swap_after_resolve_never_archives_the_victims_zip(trees, monkeypatch):
    from narranexus.platform.bundle import skill_backup
    from narranexus.platform.utils import workspace_paths

    archives = trees["tmp"] / "skill_archives"
    monkeypatch.setattr(skill_backup, "SKILL_ARCHIVES_ROOT", archives)
    monkeypatch.setattr(skill_backup, "_agent_workspace_root", lambda a, u: trees["own"])
    _skill_zip(trees["own"] / "dir" / "s.zip", b"mine")
    _skill_zip(trees["vic"] / "secret" / "s.zip", SECRET)
    real = workspace_paths.resolve_inside_workspace

    def swapping(ws, cand):
        out = real(ws, cand)
        _swap_dir_for_link(trees["own"] / "dir", trees["vic"] / "secret")
        return out

    monkeypatch.setattr(workspace_paths, "resolve_inside_workspace", swapping)
    with pytest.raises(ValueError):
        await skill_backup.archive_local_zip("u1", "agent_own", "demo", "dir/s.zip")
    assert not archives.exists() or not any(archives.rglob("*.zip"))


@pytest.mark.asyncio
async def test_archive_local_zip_archives_the_owners_zip(trees, monkeypatch):
    from narranexus.platform.bundle import skill_backup

    archives = trees["tmp"] / "skill_archives"
    monkeypatch.setattr(skill_backup, "SKILL_ARCHIVES_ROOT", archives)
    monkeypatch.setattr(skill_backup, "_agent_workspace_root", lambda a, u: trees["own"])
    _skill_zip(trees["own"] / "dir" / "s.zip", b"mine")
    path, sha = await skill_backup.archive_local_zip("u1", "agent_own", "demo", "dir/s.zip")
    assert zipfile.ZipFile(path).read("marker.txt") == b"mine"
    assert sha


@pytest.mark.asyncio
async def test_list_unbackedup_never_lists_a_swapped_skills_dir(trees, monkeypatch, db_client):
    from narranexus.platform.bundle import skill_backup
    import narranexus.platform.bundle.skill_backup as sb

    monkeypatch.setattr(sb, "get_db_client", lambda: _async(db_client))
    monkeypatch.setattr(skill_backup, "_agent_workspace_root", lambda a, u: trees["own"])
    (trees["vic"] / "skills" / "victim-skill").mkdir(parents=True)
    os.symlink(trees["vic"] / "skills", trees["own"] / "skills")
    assert await skill_backup.list_unbackedup("u1", "agent_own") == []
    os.unlink(trees["own"] / "skills")
    (trees["own"] / "skills" / "mine").mkdir(parents=True)
    assert await skill_backup.list_unbackedup("u1", "agent_own") == ["mine"]


async def _async(v):
    return v


# ── skill content hash / reconciler ──────────────────────────────────────────


def test_content_hash_is_stable_ignores_meta_and_never_follows_links(trees):
    from narranexus.platform.marketplace._skill_marketplace_impl.install_pipeline import compute_content_hash

    d = trees["own"] / "skills" / "demo"
    (d / "sub").mkdir(parents=True)
    (d / "SKILL.md").write_bytes(b"# demo")
    (d / "sub" / "x.txt").write_bytes(b"x")
    h1 = compute_content_hash(d)
    (d / ".skill_meta.json").write_text("{}", encoding="utf-8")
    assert compute_content_hash(d) == h1
    os.symlink(trees["vic"] / "secret" / "a.txt", d / "leak.txt")
    assert compute_content_hash(d) == h1  # a link contributes nothing
    # the digest is the documented one: sorted(relpath bytes + file bytes)
    import hashlib

    expect = hashlib.sha256()
    for rel, data in (("SKILL.md", b"# demo"), ("sub/x.txt", b"x")):
        expect.update(rel.encode())
        expect.update(data)
    assert h1 == f"sha256:{expect.hexdigest()}"


def test_content_hash_of_a_swapped_dir_refuses_instead_of_hashing_the_victim(trees):
    from narranexus.platform.marketplace._skill_marketplace_impl.install_pipeline import compute_content_hash

    (trees["vic"] / "skills" / "demo").mkdir(parents=True)
    (trees["vic"] / "skills" / "demo" / "SKILL.md").write_bytes(SECRET)
    os.symlink(trees["vic"] / "skills", trees["own"] / "skills")
    with pytest.raises((ValueError, OSError)):
        compute_content_hash(trees["own"] / "skills" / "demo")


@pytest.mark.asyncio
async def test_reconcile_all_ignores_a_workspace_dir_replaced_by_a_link(trees, db_client, monkeypatch):
    from narranexus.platform.services.skill_sync_service import SkillSyncService

    (trees["vic"] / "skills" / "victim-skill").mkdir(parents=True)
    # u1's tenant plants `agent_evil` -> the victim's workspace
    os.symlink(trees["vic"], trees["base"] / "u1" / "agent_evil")
    seen = []

    async def fake_reconcile(self, agent_id, user_id):
        seen.append((user_id, agent_id))
        return {"added": 0, "external_removed": 0, "modified": 0, "disabled": 0, "restored": 0}

    monkeypatch.setattr(SkillSyncService, "reconcile_workspace", fake_reconcile)
    (trees["own"] / "skills").mkdir()
    totals = await SkillSyncService(db_client).reconcile_all()
    assert ("u1", "agent_evil") not in seen
    assert ("u1", "agent_own") in seen and ("u2", "agent_vic") in seen  # the two real workspaces
    assert totals["workspaces"] == 2


# ── arena skill install ──────────────────────────────────────────────────────


def _creds():
    from backend.integrations.arena.arena_onboarding import ArenaCredentials

    return ArenaCredentials(
        api_key="arena_sk_xyz", agent_id="agent_ARENA", agent_name="Swift", claim_token="t",
        claim_url="u", referral_code=None,
    )


def test_arena_install_writes_the_owners_skill_with_a_private_credentials_file(trees):
    from backend.integrations.arena.arena_onboarding import ArenaOnboarder

    class _NoHttp:
        pass

    onb = ArenaOnboarder(http_client=_NoHttp())
    result = onb.install_skill(trees["own"] / "skills", _creds(), skill_md="# Arena\n", owner_user_id="u1")
    cred = result.skill_dir / "credentials.json"
    assert os.stat(cred).st_mode & 0o777 == 0o600
    assert sorted(os.listdir(result.skill_dir)) == [".skill_meta.json", "SKILL.md", "arena_profile.json", "credentials.json"]


def test_arena_install_never_writes_credentials_through_a_swapped_skills_dir(trees):
    from backend.integrations.arena.arena_onboarding import ArenaOnboarder

    class _NoHttp:
        pass

    (trees["vic"] / "skills").mkdir()
    os.symlink(trees["vic"] / "skills", trees["own"] / "skills")
    with pytest.raises(ValueError):
        ArenaOnboarder(http_client=_NoHttp()).install_skill(
            trees["own"] / "skills", _creds(), skill_md="# Arena\n", owner_user_id="u1"
        )
    assert os.listdir(trees["vic"] / "skills") == []


# ── linear time / off the event loop ─────────────────────────────────────────


def _deep_skill(path: Path, depth: int) -> None:
    fd = os.open(path.parent, os.O_RDONLY)
    try:
        os.mkdir(path.name, dir_fd=fd)
        cur = os.open(path.name, os.O_RDONLY | os.O_DIRECTORY, dir_fd=fd)
        for _ in range(depth):
            os.mkdir("d", dir_fd=cur)
            nxt = os.open("d", os.O_RDONLY | os.O_DIRECTORY, dir_fd=cur)
            os.close(cur)
            cur = nxt
        os.close(cur)
    finally:
        os.close(fd)


def test_content_hash_of_a_deep_tree_is_fast_within_the_cap_and_refuses_beyond_it(trees):
    import time

    from narranexus.platform.marketplace._skill_marketplace_impl.install_pipeline import compute_content_hash
    from narranexus.platform.utils import confined_io

    skills = trees["own"] / "skills"
    skills.mkdir()
    _deep_skill(skills / "ok", 60)
    start = time.monotonic()
    assert compute_content_hash(skills / "ok").startswith("sha256:")
    assert time.monotonic() - start < 2.0
    _deep_skill(skills / "too_deep", 90)
    with pytest.raises(confined_io.TreeTooDeepError):
        compute_content_hash(skills / "too_deep")


def test_export_of_a_too_deep_tree_fails_loudly_instead_of_dropping_content(trees):
    from narranexus.platform.bundle import builder
    from narranexus.platform.utils import confined_io

    _deep_skill(trees["own"] / "deep", 90)
    with pytest.raises(confined_io.TreeTooDeepError):
        builder._pack_workspace_sync(trees["own"], trees["tmp"] / "o.tar.gz", set())


@pytest.mark.asyncio
async def test_reconcile_hashes_skills_off_the_event_loop(trees, db_client, monkeypatch):
    import threading

    from narranexus.platform.services.skill_sync_service import SkillSyncService

    skill = trees["own"] / "skills" / "demo"
    skill.mkdir(parents=True)
    (skill / "SKILL.md").write_bytes(b"# demo")
    seen = []
    real = SkillSyncService._live_status

    def recording(module, skill_dir):
        seen.append(threading.current_thread() is threading.main_thread())
        return real(module, skill_dir)

    monkeypatch.setattr(SkillSyncService, "_live_status", staticmethod(recording))
    await SkillSyncService(db_client).reconcile_workspace("agent_own", "u1")
    assert seen == [False]


# ── bundle import: workspace tar landing ─────────────────────────────────────


def _workspace_tar(path: Path, files: dict) -> Path:
    with tarfile.open(path, "w:gz") as tar:
        for name, data in files.items():
            ti = tarfile.TarInfo(name)
            ti.size = len(data)
            tar.addfile(ti, io.BytesIO(data))
    return path


def test_import_workspace_tar_lands_rewrites_and_merges_for_the_owner(trees):
    from narranexus.platform.bundle import importer

    target = trees["base"] / "u1" / "agent_new"
    (target / "sub").mkdir(parents=True)
    (target / "old.txt").write_bytes(b"old")
    (target / "notes.md").write_bytes(b"stale")
    tar = _workspace_tar(
        trees["tmp"] / "ws.tar.gz",
        {"./notes.md": b"owner=<original_owner>", "./sub/a.txt": b"a", "./new.bin": b"\x00\x01"},
    )
    importer._import_workspace_tar(tar, target, str(trees["base"]), {}, "u1")
    assert (target / "notes.md").read_bytes() == b"owner=u1"  # placeholder rewritten, file replaced
    assert (target / "sub" / "a.txt").read_bytes() == b"a"
    assert (target / "new.bin").read_bytes() == b"\x00\x01"
    assert (target / "old.txt").read_bytes() == b"old"  # merged, not wiped
    assert not any(n.startswith(".narra_tmp_") for n in os.listdir(target))


def test_import_workspace_tar_into_a_preplanted_link_writes_nothing_in_the_victim(trees):
    """The per-user executor mounts all of {base}/{user}: a tenant can create
    {user}/{new_agent_id} as a link BEFORE the import runs."""
    from narranexus.platform.bundle import importer

    victim_ws = trees["vic"]
    before = sorted(os.listdir(victim_ws))
    target = trees["base"] / "u1" / "agent_new"
    os.symlink(victim_ws, target)
    tar = _workspace_tar(trees["tmp"] / "ws.tar.gz", {"./pwn.txt": b"attacker bundle content", "./secret/a.txt": b"x"})
    with pytest.raises(ValueError):
        importer._import_workspace_tar(tar, target, str(trees["base"]), {}, "u1")
    assert sorted(os.listdir(victim_ws)) == before
    assert (victim_ws / "secret" / "a.txt").read_bytes() == SECRET


def test_import_workspace_tar_never_writes_through_links_inside_the_target(trees):
    from narranexus.platform.bundle import importer

    target = trees["base"] / "u1" / "agent_new"
    target.mkdir(parents=True)
    victim_file = trees["vic"] / "precious.md"
    victim_file.write_bytes(SECRET)
    os.symlink(victim_file, target / "notes.md")  # a link at a file name
    os.symlink(trees["vic"] / "secret", target / "sub")  # a link at a directory name
    tar = _workspace_tar(
        trees["tmp"] / "ws.tar.gz",
        {"./notes.md": b"<original_owner> rewrite me", "./sub/a.txt": b"attacker"},
    )
    importer._import_workspace_tar(tar, target, str(trees["base"]), {}, "u1")
    assert victim_file.read_bytes() == SECRET
    assert (trees["vic"] / "secret" / "a.txt").read_bytes() == SECRET
    assert sorted(os.listdir(trees["vic"] / "secret")) == ["a.txt"]
    assert not os.path.islink(target / "notes.md") and (target / "notes.md").read_bytes() == b"u1 rewrite me"
    assert not os.path.islink(target / "sub") and (target / "sub" / "a.txt").read_bytes() == b"attacker"
