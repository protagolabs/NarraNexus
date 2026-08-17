"""
@file_name: test_skill_archive_path_safety.py
@author: NarraNexus
@date: 2026-08-17
@description: SEC-07 — `skill_archives/{user_id}/{skill_name}.*` is composed in
             seven places across three files. This pins the single chokepoint
             (`skill_backup.archive_target`) that all of them must go through,
             plus the read-side guard that keeps an already-poisoned
             `archive_path` row from exfiltrating a file into an export.

Write side: every caller passes a name that ultimately comes from outside —
a multipart Form field (route), a bundle manifest (importer), an LLM tool
argument (MCP tools). None of them may be able to leave the user's directory.

Read side: rows written before the fix (QA left one on the dev env, id=20)
still carry `../` paths. `build_bundle` copies `archive_path` into the zip it
streams back, so containment has to be re-checked at read time too — sealing
the write path does not retroactively clean the DB.
"""

from __future__ import annotations

import json
import sys
import zipfile
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO_ROOT / "src"))


# ─── write side: the chokepoint ─────────────────────────────────────────────


@pytest.fixture
def archives_root(tmp_path, monkeypatch):
    from xyz_agent_context.bundle import skill_backup

    root = tmp_path / "skill_archives"
    monkeypatch.setattr(skill_backup, "SKILL_ARCHIVES_ROOT", root)
    return root


TRAVERSAL_NAMES = [
    "../qa-sec07-oneup-marker",
    "../../etc/cron.d/qa-sec07",
    "../other_user/stolen",
    "a/b",
    "a\\b",
    "/abs/path",
    "..",
    ".",
    "",
    "nul\x00byte",
]


@pytest.mark.parametrize("skill_name", TRAVERSAL_NAMES)
def test_archive_target_rejects_traversal(archives_root, skill_name):
    from xyz_agent_context.bundle.skill_backup import archive_target

    with pytest.raises(ValueError) as exc:
        archive_target("u1", skill_name)
    assert "skill name" in str(exc.value).lower()


@pytest.mark.parametrize("suffix", [".zip", ".tar.gz", "_full.zip"])
def test_archive_target_composes_inside_the_user_dir(archives_root, suffix):
    from xyz_agent_context.bundle.skill_backup import archive_target

    p = archive_target("u1", "legit-skill", suffix=suffix)
    assert p == archives_root / "u1" / f"legit-skill{suffix}"
    assert p.parent.is_dir(), "the user dir should be created eagerly"


def test_archive_target_rejects_a_traversing_user_id(archives_root):
    """user_id comes from JWT/header resolution, but it is a path segment too."""
    from xyz_agent_context.bundle.skill_backup import archive_target

    with pytest.raises(ValueError) as exc:
        archive_target("../elsewhere", "legit-skill")
    assert "user id" in str(exc.value).lower()


def test_archive_target_rejects_symlink_escape(archives_root):
    """A symlinked user dir must not become a way out of the archives root."""
    from xyz_agent_context.bundle.skill_backup import archive_target

    outside = archives_root.parent / "outside"
    outside.mkdir(parents=True, exist_ok=True)
    archives_root.mkdir(parents=True, exist_ok=True)
    (archives_root / "u2").symlink_to(outside, target_is_directory=True)

    with pytest.raises(ValueError):
        archive_target("u2", "legit-skill")


# ─── write side: every composition site routes through the chokepoint ───────


def test_no_site_composes_an_archive_path_by_hand():
    """Regression guard: an f-string archive path anywhere in these files means
    a new bypass was added. Grep-style assertion is deliberate — this bug class
    reappears exactly by someone hand-rolling the path again."""
    suspects = [
        REPO_ROOT / "backend" / "routes" / "bundle.py",
        REPO_ROOT / "src" / "xyz_agent_context" / "bundle" / "skill_backup.py",
        REPO_ROOT / "src" / "xyz_agent_context" / "bundle" / "importer.py",
    ]
    offenders = []
    for f in suspects:
        for lineno, line in enumerate(f.read_text(encoding="utf-8").splitlines(), 1):
            stripped = line.strip()
            if stripped.startswith("#"):
                continue
            # `<dir> / f"...skill_name..."` — a Path division into an f-string
            # that interpolates the skill name is exactly the SEC-07 shape.
            if ("/ f" in stripped or "/f" in stripped) and "skill_name" in stripped:
                offenders.append(f"{f.relative_to(REPO_ROOT)}:{lineno}: {stripped}")
    assert not offenders, "hand-composed archive path(s):\n" + "\n".join(offenders)


# ─── read side: a poisoned row must not exfiltrate into an export ───────────


@pytest.fixture
def tmp_db_path(tmp_path):
    return tmp_path / "test_nexus.db"


@pytest.fixture
def tmp_workspace_root(tmp_path, monkeypatch):
    ws = tmp_path / "workspaces"
    ws.mkdir()
    fake_home = tmp_path / "home"
    fake_home.mkdir()
    from xyz_agent_context.settings import settings as core_settings

    monkeypatch.setattr(core_settings, "base_working_path", str(ws))
    monkeypatch.setenv("HOME", str(fake_home))
    return ws


@pytest.fixture
async def db_client(tmp_db_path, monkeypatch):
    from xyz_agent_context.settings import settings as core_settings

    monkeypatch.setattr(core_settings, "database_url", f"sqlite:///{tmp_db_path}")
    from xyz_agent_context.utils.db import db_factory

    db_factory._clients_by_loop.clear()
    from xyz_agent_context.utils.db.db_factory import get_db_client
    from xyz_agent_context.utils.db.schema_registry import auto_migrate

    db = await get_db_client()
    await auto_migrate(db._backend)
    yield db
    db_factory._clients_by_loop.clear()


async def _seed_agent(db, agent_id, agent_name, user_id="test_user"):
    if not await db.get_one("users", {"user_id": user_id}):
        await db.insert(
            "users",
            {
                "user_id": user_id,
                "user_type": "local",
                "role": "user",
                "display_name": "Test User",
            },
        )
    await db.insert(
        "agents",
        {
            "agent_id": agent_id,
            "agent_name": agent_name,
            "created_by": user_id,
            "agent_description": "d",
            "agent_type": "default",
        },
    )


def _seed_skill_on_disk(ws_root: Path, agent_id: str, user_id: str, skill_dir: str):
    from xyz_agent_context.utils.workspace_paths import agent_workspace_path

    d = agent_workspace_path(agent_id, user_id, base=str(ws_root)) / "skills" / skill_dir
    d.mkdir(parents=True, exist_ok=True)
    (d / "SKILL.md").write_text("---\nname: arena\n---\n\nbody\n", encoding="utf-8")
    return d


def _manifest(bundle: Path) -> dict:
    with zipfile.ZipFile(bundle) as z:
        return json.loads(z.read("manifest.json"))


def _bundle_contains(bundle: Path, needle: bytes) -> bool:
    with zipfile.ZipFile(bundle) as z:
        return any(needle in z.read(n) for n in z.namelist() if not n.endswith("/"))


async def test_poisoned_archive_path_row_is_not_packed(
    db_client, tmp_workspace_root, tmp_path, archives_root
):
    """The `../` row QA left behind must be skipped with a warning, not copied."""
    from xyz_agent_context.bundle.builder import ExportSelection, build_bundle
    from xyz_agent_context.repository import SkillArchiveRepository

    aid, uid = "agent_sec07001", "test_user"
    await _seed_agent(db_client, aid, "Sec07Agent", uid)
    _seed_skill_on_disk(tmp_workspace_root, aid, uid, "arena")

    secret = tmp_path / "victims-secret.zip"
    secret.write_bytes(b"SEC07_EXFILTRATION_CANARY")

    repo = SkillArchiveRepository(db_client)
    await repo.upsert(
        user_id=uid,
        skill_name="arena",
        source_type="zip",
        sha256="deadbeef",
        archive_path=str(secret),  # pre-fix poisoned row
    )

    bundle = tmp_path / "poisoned.nxbundle"
    result = await build_bundle(
        uid,
        ExportSelection(
            agent_ids=[aid],
            skill_methods=[
                {
                    "agent_id": aid,
                    "skill_name": "arena",
                    "skill_dir": "arena",
                    "install_method": "zip",
                }
            ],
        ),
        bundle,
    )

    assert not _bundle_contains(bundle, b"SEC07_EXFILTRATION_CANARY"), (
        "a poisoned archive_path was copied into the export"
    )
    warnings = " ".join(result.get("warnings", []))
    assert "arena" in warnings and "archive" in warnings.lower()
    entries = [s for s in _manifest(bundle).get("skills", []) if s.get("name") == "arena"]
    assert all(not e.get("archive_ref") for e in entries)


async def test_legitimate_archive_row_still_exports(
    db_client, tmp_workspace_root, tmp_path, archives_root
):
    """The guard must not break the normal zip-method export path."""
    from xyz_agent_context.bundle.builder import ExportSelection, build_bundle
    from xyz_agent_context.bundle.skill_backup import archive_target
    from xyz_agent_context.repository import SkillArchiveRepository

    aid, uid = "agent_sec07002", "test_user"
    await _seed_agent(db_client, aid, "Sec07Agent2", uid)
    _seed_skill_on_disk(tmp_workspace_root, aid, uid, "arena")

    good = archive_target(uid, "arena")
    with zipfile.ZipFile(good, "w") as z:
        z.writestr("arena/SKILL.md", "---\nname: arena\n---\nlegit\n")

    repo = SkillArchiveRepository(db_client)
    await repo.upsert(
        user_id=uid,
        skill_name="arena",
        source_type="zip",
        sha256="cafebabe",
        archive_path=str(good),
    )

    bundle = tmp_path / "clean.nxbundle"
    await build_bundle(
        uid,
        ExportSelection(
            agent_ids=[aid],
            skill_methods=[
                {
                    "agent_id": aid,
                    "skill_name": "arena",
                    "skill_dir": "arena",
                    "install_method": "zip",
                }
            ],
        ),
        bundle,
    )

    entries = [s for s in _manifest(bundle).get("skills", []) if s.get("name") == "arena"]
    assert entries and entries[0].get("archive_ref"), "legit archive was not packed"
    assert _bundle_contains(bundle, b"legit")
