"""
@file_name: test_corrupt_archive_export.py
@author: NarraNexus
@date: 2026-08-18
@description: A corrupt archive must degrade to a warning, not a 500.

Found on the dev env while verifying the SEC-07 fix: uploading bytes that are
not a zip was accepted (200), and the failure surfaced one endpoint away —
`POST /api/bundle/export` raised `BadZipFile` out of `scan_zip_for_sensitive`
and became a 500 whose message ("Failed to build the export") names neither the
skill nor the archive. Same shape as #113.

The upload gate (see `tests/backend/test_bundle_archive_path_safety.py`) stops
new bad archives. This file covers the other half: rows that are ALREADY bad —
written before the gate existed, truncated by a half-finished write, or
corrupted on disk — must not be able to take a whole export down with them.
"""

from __future__ import annotations

import json
import sys
import zipfile
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO_ROOT / "src"))


@pytest.fixture
def archives_root(tmp_path, monkeypatch):
    from xyz_agent_context.bundle import skill_backup

    root = tmp_path / "skill_archives"
    monkeypatch.setattr(skill_backup, "SKILL_ARCHIVES_ROOT", root)
    return root


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
    (d / "SKILL.md").write_text(f"---\nname: {skill_dir}\n---\n\nbody\n", encoding="utf-8")
    return d


def _manifest(bundle: Path) -> dict:
    with zipfile.ZipFile(bundle) as z:
        return json.loads(z.read("manifest.json"))


async def test_corrupt_archive_is_skipped_with_a_warning_not_a_500(
    db_client, tmp_workspace_root, tmp_path, archives_root
):
    """One unreadable archive → that skill is skipped, the export still builds,
    and the warning names the skill so the user can act on it."""
    from xyz_agent_context.bundle.builder import ExportSelection, build_bundle
    from xyz_agent_context.bundle.skill_backup import prepare_archive_target
    from xyz_agent_context.repository import SkillArchiveRepository

    aid, uid = "agent_corrupt01", "test_user"
    await _seed_agent(db_client, aid, "CorruptAgent", uid)
    _seed_skill_on_disk(tmp_workspace_root, aid, uid, "brokenskill")

    bad = prepare_archive_target(uid, "brokenskill")
    bad.write_bytes(b"PK\x03\x04definitely not a zip")

    await SkillArchiveRepository(db_client).upsert(
        user_id=uid, skill_name="brokenskill", source_type="zip",
        sha256="deadbeef", archive_path=str(bad),
    )

    bundle = tmp_path / "corrupt.nxbundle"
    result = await build_bundle(
        uid,
        ExportSelection(
            agent_ids=[aid],
            skill_methods=[
                {"agent_id": aid, "skill_name": "brokenskill",
                 "skill_dir": "brokenskill", "install_method": "zip"}
            ],
        ),
        bundle,
    )

    assert bundle.exists(), "the export must still be produced"
    warnings = " ".join(result.get("warnings", []))
    assert "brokenskill" in warnings, f"warning must name the skill: {warnings!r}"
    assert "zip" in warnings.lower()
    # Dropped from the manifest entirely — the same shape as the "zip not found"
    # path right above it in the builder, so the importer sees one convention.
    entries = [s for s in _manifest(bundle).get("skills", []) if s.get("name") == "brokenskill"]
    assert entries == [], f"corrupt archive must not be packed, got {entries}"
    with zipfile.ZipFile(bundle) as z:
        assert not [n for n in z.namelist() if "brokenskill" in n], "bad bytes shipped anyway"


async def test_one_corrupt_archive_does_not_sink_the_healthy_ones(
    db_client, tmp_workspace_root, tmp_path, archives_root
):
    """The reason this matters: before the fix, a single bad row failed the
    WHOLE export, so one user's stale archive blocked everything else too."""
    from xyz_agent_context.bundle.builder import ExportSelection, build_bundle
    from xyz_agent_context.bundle.skill_backup import prepare_archive_target
    from xyz_agent_context.repository import SkillArchiveRepository

    aid, uid = "agent_corrupt02", "test_user"
    await _seed_agent(db_client, aid, "MixedAgent", uid)
    for name in ("brokenskill", "goodskill"):
        _seed_skill_on_disk(tmp_workspace_root, aid, uid, name)

    repo = SkillArchiveRepository(db_client)

    bad = prepare_archive_target(uid, "brokenskill")
    bad.write_bytes(b"not a zip at all")
    await repo.upsert(user_id=uid, skill_name="brokenskill", source_type="zip",
                      sha256="deadbeef", archive_path=str(bad))

    good = prepare_archive_target(uid, "goodskill")
    with zipfile.ZipFile(good, "w") as z:
        z.writestr("goodskill/SKILL.md", "---\nname: goodskill\n---\nHEALTHY_CANARY\n")
    await repo.upsert(user_id=uid, skill_name="goodskill", source_type="zip",
                      sha256="cafebabe", archive_path=str(good))

    bundle = tmp_path / "mixed.nxbundle"
    await build_bundle(
        uid,
        ExportSelection(
            agent_ids=[aid],
            skill_methods=[
                {"agent_id": aid, "skill_name": n, "skill_dir": n, "install_method": "zip"}
                for n in ("brokenskill", "goodskill")
            ],
        ),
        bundle,
    )

    skills = {s.get("name"): s for s in _manifest(bundle).get("skills", [])}
    assert "goodskill" in skills, f"healthy skill was dropped too: {sorted(skills)}"
    assert skills["goodskill"].get("archive_ref"), "healthy skill lost its archive"
    assert "brokenskill" not in skills, "corrupt skill should be dropped, not packed"
    with zipfile.ZipFile(bundle) as z:
        blob = b"".join(z.read(n) for n in z.namelist() if not n.endswith("/"))
    assert b"HEALTHY_CANARY" in blob


async def test_tarball_archive_gets_a_message_that_points_at_the_real_mistake(
    db_client, tmp_workspace_root, tmp_path, archives_root
):
    """A github-installed skill's archive is a real `.tar.gz`. Exporting it with
    `install_method="zip"` must not tell the user their archive is broken — it
    isn't; the method is wrong. `archive_rows_by_skill` keeps `source_type` so
    the warning can say so."""
    from xyz_agent_context.bundle.builder import ExportSelection, build_bundle
    from xyz_agent_context.bundle.skill_backup import prepare_archive_target
    from xyz_agent_context.repository import SkillArchiveRepository

    aid, uid = "agent_tarball01", "test_user"
    await _seed_agent(db_client, aid, "TarballAgent", uid)
    _seed_skill_on_disk(tmp_workspace_root, aid, uid, "githubskill")

    tgz = prepare_archive_target(uid, "githubskill", suffix=".tar.gz")
    tgz.write_bytes(b"\x1f\x8b\x08\x00fake but genuinely a tarball, not a zip")
    await SkillArchiveRepository(db_client).upsert(
        user_id=uid, skill_name="githubskill", source_type="github",
        source_url="https://github.com/o/r", sha256="abc123", archive_path=str(tgz),
    )

    result = await build_bundle(
        uid,
        ExportSelection(
            agent_ids=[aid],
            skill_methods=[
                {"agent_id": aid, "skill_name": "githubskill",
                 "skill_dir": "githubskill", "install_method": "zip"}
            ],
        ),
        tmp_path / "tarball.nxbundle",
    )
    warnings = " ".join(result.get("warnings", []))
    assert "githubskill" in warnings
    assert "github" in warnings and "url" in warnings, (
        f"warning should point at the method/source mismatch, got: {warnings!r}"
    )
    assert "not a readable zip" not in warnings, "blames the file for a method error"


async def test_failure_midway_through_copy_leaves_no_partial_archive(
    db_client, tmp_workspace_root, tmp_path, archives_root, monkeypatch
):
    """The degrade path covers copy2/file_sha256, not just the scan — and it
    cleans up. A half-written `{skill_dir}.zip` would otherwise be picked up by
    the `tgt_zip.exists()` branch for the next entry with that dir name and
    silently push it onto the `__{agent_id}` filename."""
    from xyz_agent_context.bundle import builder as builder_mod
    from xyz_agent_context.bundle.builder import ExportSelection, build_bundle
    from xyz_agent_context.bundle.skill_backup import prepare_archive_target
    from xyz_agent_context.repository import SkillArchiveRepository

    aid, uid = "agent_copyfail01", "test_user"
    await _seed_agent(db_client, aid, "CopyFailAgent", uid)
    _seed_skill_on_disk(tmp_workspace_root, aid, uid, "flakyskill")

    good = prepare_archive_target(uid, "flakyskill")
    with zipfile.ZipFile(good, "w") as z:
        z.writestr("flakyskill/SKILL.md", "---\nname: flakyskill\n---\nok\n")
    await SkillArchiveRepository(db_client).upsert(
        user_id=uid, skill_name="flakyskill", source_type="zip",
        sha256="cafebabe", archive_path=str(good),
    )

    def _half_copy(src, dst, *a, **kw):
        Path(dst).write_bytes(b"PARTIAL")      # leave a stub behind, then fail
        raise OSError("disk went away mid-copy")

    # builder_mod.shutil IS the stdlib module object, so this patches copy2
    # process-wide for the duration; monkeypatch restores it at teardown.
    monkeypatch.setattr(builder_mod.shutil, "copy2", _half_copy)

    bundle = tmp_path / "copyfail.nxbundle"
    result = await build_bundle(
        uid,
        ExportSelection(
            agent_ids=[aid],
            skill_methods=[
                {"agent_id": aid, "skill_name": "flakyskill",
                 "skill_dir": "flakyskill", "install_method": "zip"}
            ],
        ),
        bundle,
    )
    assert bundle.exists(), "a copy failure must not fail the whole export"
    assert "flakyskill" in " ".join(result.get("warnings", []))
    with zipfile.ZipFile(bundle) as z:
        leftovers = [n for n in z.namelist() if "flakyskill" in n and n.endswith(".zip")]
    assert leftovers == [], f"partial archive shipped: {leftovers}"
