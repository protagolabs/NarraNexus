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


def test_archive_target_is_pure(archives_root):
    """It must not mkdir. The route's "a 4xx leaves no trace" promise depends on
    validation being side-effect free — with the mkdir inside, every later 400
    (bad source_type, missing file, oversize) still littered a user dir."""
    from xyz_agent_context.bundle.skill_backup import archive_target

    archive_target("u1", "legit-skill")
    assert not archives_root.exists(), "validation created directories"


def test_prepare_archive_target_creates_the_parent(archives_root):
    """The write-time variant is the one that may touch the filesystem."""
    from xyz_agent_context.bundle.skill_backup import prepare_archive_target

    p = prepare_archive_target("u1", "legit-skill")
    assert p.parent.is_dir()
    assert not p.exists(), "only the dir, never the file"


def test_archive_target_rejects_a_traversing_user_id(archives_root):
    """user_id comes from JWT/header resolution, but it is a path segment too."""
    from xyz_agent_context.bundle.skill_backup import archive_target

    with pytest.raises(ValueError) as exc:
        archive_target("../elsewhere", "legit-skill")
    assert "user id" in str(exc.value).lower()


def test_read_guard_rejects_symlinked_user_dir(archives_root):
    """Read side keeps the archives-root anchor too, so a `{root}/{uid}` symlink
    pointing out of the tree cannot make "inside the user's dir" vacuously true.
    Today the write side already refuses such a dir — this is the depth that
    stops mattering the moment some other path learns to write the column."""
    from xyz_agent_context.bundle.skill_backup import is_within_user_archive_dir

    outside = archives_root.parent / "outside"
    outside.mkdir(parents=True, exist_ok=True)
    archives_root.mkdir(parents=True, exist_ok=True)
    (archives_root / "u3").symlink_to(outside, target_is_directory=True)
    escaped = outside / "loot.zip"
    escaped.write_bytes(b"x")

    assert not is_within_user_archive_dir("u3", escaped)
    # …while an ordinary user dir still passes.
    (archives_root / "u4").mkdir()
    assert is_within_user_archive_dir("u4", archives_root / "u4" / "ok.zip")


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
    """Regression guard on the SHAPE of the bug, not on a known line.

    Scope, stated honestly so nobody over-trusts a green run. It flags a line
    that mentions `skill_name` AND looks like path composition (`/`, with or
    without spaces; `.joinpath(`; `os.path.join(`), anywhere in the bundle
    package or the bundle route. Composition in argument position counts —
    `copy2(src, base / f"{skill_name}.zip")` is caught, not just assignments.
    Exemptions are narrow and by shape, never by line number: the sanctioned
    builders (`archive_target` / `prepare_archive_target`) and log/label lines
    (`logger.…`, or an f-string fed to `append(`), which is where
    `f"{skill_name}@{old_aid}"` lives.

    What it still cannot see: composition that renames the variable first
    (`name = s.get("name")`), or that splits across lines. It catches the shape
    this bug class actually recurs in — it is not a proof of absence, so do not
    skip a manual sweep because it is green.
    """
    suspects = [
        REPO_ROOT / "backend" / "routes" / "bundle.py",
        *sorted((REPO_ROOT / "src" / "xyz_agent_context" / "bundle").glob("*.py")),
    ]

    def _is_path_composition(line: str) -> bool:
        if "archive_target(" in line:  # the sanctioned builders
            return False
        if line.startswith("logger.") or "append(" in line:  # log / label lines
            return False
        return ("/" in line) or (".joinpath(" in line) or ("os.path.join(" in line)

    offenders = []
    for f in suspects:
        for lineno, line in enumerate(f.read_text(encoding="utf-8").splitlines(), 1):
            stripped = line.strip()
            if stripped.startswith("#"):
                continue
            if "skill_name" in stripped and _is_path_composition(stripped):
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


async def _export_with_poisoned_row(db_client, ws_root, tmp_path, poisoned_path: Path, tag: str):
    """Seed one poisoned `skill_archives` row, export, return (result, bundle)."""
    from xyz_agent_context.bundle.builder import ExportSelection, build_bundle
    from xyz_agent_context.repository import SkillArchiveRepository

    aid, uid = f"agent_{tag}", "test_user"
    await _seed_agent(db_client, aid, f"Agent{tag}", uid)
    _seed_skill_on_disk(ws_root, aid, uid, "arena")

    await SkillArchiveRepository(db_client).upsert(
        user_id=uid,
        skill_name="arena",
        source_type="zip",
        sha256="deadbeef",
        archive_path=str(poisoned_path),
    )

    bundle = tmp_path / f"{tag}.nxbundle"
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
    return result, bundle


def _assert_skipped(result, bundle):
    assert not _bundle_contains(bundle, b"SEC07_EXFILTRATION_CANARY"), (
        "a poisoned archive_path was copied into the export"
    )
    warnings = " ".join(result.get("warnings", []))
    assert "arena" in warnings and "archive" in warnings.lower()
    entries = [s for s in _manifest(bundle).get("skills", []) if s.get("name") == "arena"]
    assert all(not e.get("archive_ref") for e in entries)


async def test_absolute_outside_path_row_is_not_packed(
    db_client, tmp_workspace_root, tmp_path, archives_root
):
    """`archive_path: "/etc/passwd"` shape — the read-side vector from /export."""
    secret = tmp_path / "victims-secret.zip"
    secret.write_bytes(b"SEC07_EXFILTRATION_CANARY")
    result, bundle = await _export_with_poisoned_row(
        db_client, tmp_workspace_root, tmp_path, secret, "outside"
    )
    _assert_skipped(result, bundle)


async def test_root_level_stray_row_is_not_packed(
    db_client, tmp_workspace_root, tmp_path, archives_root
):
    """The dev env's actual id=20 shape, and why a ROOT-anchored guard is not
    enough: the stored string `{root}/{uid}/../marker.zip` resolves to
    `{root}/marker.zip` — inside the archives root, outside anyone's user dir.
    A root-anchored check waves this exact row through."""
    stray = archives_root / "qa-sec07-oneup-marker.zip"
    stray.parent.mkdir(parents=True, exist_ok=True)
    stray.write_bytes(b"SEC07_EXFILTRATION_CANARY")
    result, bundle = await _export_with_poisoned_row(
        db_client, tmp_workspace_root, tmp_path, stray, "stray"
    )
    _assert_skipped(result, bundle)


async def test_other_users_archive_row_is_not_packed(
    db_client, tmp_workspace_root, tmp_path, archives_root
):
    """Cross-user read: a row naming ANOTHER user's archive dir. Also inside the
    root, so likewise invisible to a root-anchored guard."""
    victim = archives_root / "other_user" / "private.zip"
    victim.parent.mkdir(parents=True, exist_ok=True)
    victim.write_bytes(b"SEC07_EXFILTRATION_CANARY")
    result, bundle = await _export_with_poisoned_row(
        db_client, tmp_workspace_root, tmp_path, victim, "crossuser"
    )
    _assert_skipped(result, bundle)


async def test_legitimate_archive_row_still_exports(
    db_client, tmp_workspace_root, tmp_path, archives_root
):
    """The guard must not break the normal zip-method export path."""
    from xyz_agent_context.bundle.builder import ExportSelection, build_bundle
    from xyz_agent_context.bundle.skill_backup import prepare_archive_target
    from xyz_agent_context.repository import SkillArchiveRepository

    aid, uid = "agent_sec07002", "test_user"
    await _seed_agent(db_client, aid, "Sec07Agent2", uid)
    _seed_skill_on_disk(tmp_workspace_root, aid, uid, "arena")

    good = prepare_archive_target(uid, "arena")
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


# ─── importing a zip-method skill must leave a usable archive row ───────────


async def test_imported_zip_skill_registers_archive(
    db_client, tmp_workspace_root, tmp_path, archives_root
):
    """Export zip-method → import → the receiving user must have a
    `skill_archives` row pointing at the archive on disk.

    This was silently broken: the importer handed its own already-in-place
    `tgt` to `backup_after_api_install`, which recomputed the same path and did
    `copy2(tgt, tgt)` → `SameFileError` → swallowed by that helper's broad
    `except` → no row written. Consequence: an imported skill could never be
    re-exported as `zip`, it degraded to `full_copy` — which ships secrets when
    the user picks full mode. Now the importer registers `tgt` directly.
    """
    from xyz_agent_context.bundle.builder import ExportSelection, build_bundle
    from xyz_agent_context.bundle.importer import preflight, confirm
    from xyz_agent_context.bundle.skill_backup import prepare_archive_target
    from xyz_agent_context.repository import SkillArchiveRepository

    aid, uid = "agent_sec07003", "test_user"
    await _seed_agent(db_client, aid, "Sec07Agent3", uid)
    _seed_skill_on_disk(tmp_workspace_root, aid, uid, "arena")

    src = prepare_archive_target(uid, "arena")
    with zipfile.ZipFile(src, "w") as z:
        z.writestr("arena/SKILL.md", "---\nname: arena\n---\nlegit\n")
    repo = SkillArchiveRepository(db_client)
    await repo.upsert(
        user_id=uid, skill_name="arena", source_type="zip", sha256="cafebabe",
        archive_path=str(src),
    )

    bundle = tmp_path / "zip-method.nxbundle"
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

    # Drop the row so the import has to create it (models a fresh target env).
    await repo.remove(uid, "arena")
    assert await repo.get(uid, "arena") is None

    pre = await preflight(bundle, uid)
    summary = await confirm(pre["preflight_token"], uid)
    assert summary.get("skills_imported", 0) == 1, summary.get("warnings")

    row = await repo.get(uid, "arena")
    assert row is not None, "import left no skill_archives row (SameFileError swallowed?)"
    assert row.archive_path and Path(row.archive_path).exists()
    assert row.sha256 and row.sha256 != "pending"


async def test_shared_skill_import_records_a_real_sha(
    db_client, tmp_workspace_root, tmp_path, archives_root
):
    """Two agents sharing one zip skill: the imported row must carry a real
    digest, not the bundle's de-dup sentinel.

    `builder` emits one manifest entry per (agent, skill) and marks entries
    2..N `sha256: "shared"` because they point at one already-copied
    archive_ref. The importer registers every entry (upsert, last write wins),
    so taking the manifest value verbatim persisted the literal `"shared"` into
    `skill_archives.sha256` — a column whose only job is integrity.
    """
    import re

    from xyz_agent_context.bundle.builder import ExportSelection, build_bundle
    from xyz_agent_context.bundle.importer import preflight, confirm
    from xyz_agent_context.bundle.skill_backup import prepare_archive_target
    from xyz_agent_context.repository import SkillArchiveRepository

    uid = "test_user"
    aids = ["agent_sec07004", "agent_sec07005"]
    for i, aid in enumerate(aids):
        await _seed_agent(db_client, aid, f"SharedAgent{i}", uid)
        _seed_skill_on_disk(tmp_workspace_root, aid, uid, "arena")

    src = prepare_archive_target(uid, "arena")
    with zipfile.ZipFile(src, "w") as z:
        z.writestr("arena/SKILL.md", "---\nname: arena\n---\nlegit\n")
    repo = SkillArchiveRepository(db_client)
    await repo.upsert(
        user_id=uid, skill_name="arena", source_type="zip", sha256="cafebabe",
        archive_path=str(src),
    )

    bundle = tmp_path / "shared.nxbundle"
    await build_bundle(
        uid,
        ExportSelection(
            agent_ids=aids,
            skill_methods=[
                {"agent_id": aid, "skill_name": "arena", "skill_dir": "arena",
                 "install_method": "zip"}
                for aid in aids
            ],
        ),
        bundle,
    )

    # Precondition: the bundle really does carry the sentinel we're guarding
    # against — otherwise this test would pass for the wrong reason.
    shas = [s.get("sha256") for s in _manifest(bundle).get("skills", [])
            if s.get("name") == "arena"]
    assert "shared" in shas, f"expected a de-dup sentinel in the manifest, got {shas}"

    await repo.remove(uid, "arena")
    pre = await preflight(bundle, uid)
    await confirm(pre["preflight_token"], uid)

    row = await repo.get(uid, "arena")
    assert row is not None
    assert re.fullmatch(r"[0-9a-f]{64}", row.sha256 or ""), (
        f"skill_archives.sha256 is not a digest: {row.sha256!r}"
    )
