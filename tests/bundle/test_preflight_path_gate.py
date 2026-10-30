"""
@file_name: test_preflight_path_gate.py
@author: Bin Liang
@date: 2026-10-30
@description: Bundle-controlled path strings are refused at PREFLIGHT, not at
confirm.

2026-10-29: an attacker uploaded bundles whose artifacts.json `file_path`
values climbed out of the new agent's workspace (another tenant's file,
/etc/passwd). Confirm refused them and rolled back, but preflight had already
answered 200 — telling the prober the upload was accepted and leaving the
unpacked bundle on disk for the TTL. Every path-shaped string a bundle carries
(`artifacts.json` file_path, a skill's `archive_ref`) is now checked before
preflight issues a token: a bad one rejects the whole bundle, removes the
unpack dir, and emits the boundary-violation log line.
"""
from __future__ import annotations

import json
import zipfile
from pathlib import Path

import pytest
from loguru import logger

from narranexus.platform.bundle.importer import preflight
from narranexus.platform.utils.logging._security import BOUNDARY_MARKER

AID = "agent_1234567890ab"


@pytest.fixture
def tmp_home(tmp_path, monkeypatch):
    ws = tmp_path / "workspaces"
    ws.mkdir()
    home = tmp_path / "home"
    home.mkdir()
    from narranexus.platform.settings import settings as core_settings
    monkeypatch.setattr(core_settings, "base_working_path", str(ws))
    monkeypatch.setenv("HOME", str(home))
    return home


@pytest.fixture
async def isolated_db(tmp_path, tmp_home, monkeypatch):
    from narranexus.platform.settings import settings as core_settings
    monkeypatch.setattr(core_settings, "database_url", f"sqlite:///{tmp_path / 'db.sqlite'}")
    from narranexus.platform.utils.db import db_factory
    db_factory._clients_by_loop.clear()
    from narranexus.platform.utils.db.db_factory import get_db_client
    from narranexus.platform.utils.db.schema_registry import auto_migrate
    db = await get_db_client()
    await auto_migrate(db._backend)
    yield db
    db_factory._clients_by_loop.clear()


@pytest.fixture
def boundary_lines():
    captured: list[str] = []
    sink_id = logger.add(captured.append, level="WARNING", format="{message}")
    yield lambda: [line for line in captured if BOUNDARY_MARKER in line]
    logger.remove(sink_id)


def _bundle(tmp_path: Path, *, artifacts=None, skills=None) -> Path:
    path = tmp_path / "upload.nxbundle"
    manifest = {"bundle_format_version": "1.0", "agents": [AID], "skills": skills or []}
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("manifest.json", json.dumps(manifest))
        zf.writestr(f"agents/{AID}/agent.json", json.dumps({"agent_id": AID, "agent_name": "probe"}))
        if artifacts is not None:
            zf.writestr(f"agents/{AID}/artifacts.json", json.dumps(artifacts))
    return path


def _sessions_root(home: Path) -> Path:
    return home / ".nexusagent" / "bundle_preflight"


ESCAPING_FILE_PATHS = [
    "../other_user/agent_x/config.json",
    "../../../../../etc/passwd",
    "sub/../../escape.txt",
    "a\\..\\b",
    "nul\x00byte",
]


@pytest.mark.parametrize("bad", ESCAPING_FILE_PATHS)
async def test_escaping_artifact_path_rejects_at_preflight(
    isolated_db, tmp_home, tmp_path, boundary_lines, bad
):
    bundle = _bundle(tmp_path, artifacts=[
        {"artifact_id": "art_ok", "file_path": "README.md"},
        {"artifact_id": "art_bad", "file_path": bad},
    ])
    with pytest.raises(ValueError, match="artifact"):
        await preflight(bundle, "u_attacker")

    # No token was issued and nothing is left on disk for the TTL.
    assert await isolated_db.get("bundle_preflight_sessions", {"user_id": "u_attacker"}) == []
    assert list(_sessions_root(tmp_home).glob("nx-import-*")) == []
    hits = boundary_lines()
    assert len(hits) == 1
    assert "surface=bundle_import.preflight" in hits[0]
    assert "actor=u_attacker" in hits[0]


ESCAPING_ARCHIVE_REFS = [
    "../../nx-import-other/agents/x/channel_credentials.json",
    "../../../../etc/hosts.zip",
    "/etc/passwd",
    "skills/../../../outside.zip",
]


@pytest.mark.parametrize("method", ["zip", "full_copy"])
@pytest.mark.parametrize("ref", ESCAPING_ARCHIVE_REFS)
async def test_escaping_archive_ref_rejects_at_preflight(
    isolated_db, tmp_home, tmp_path, boundary_lines, method, ref
):
    bundle = _bundle(tmp_path, skills=[{
        "name": "s1", "install_method": method, "agent_id": AID, "archive_ref": ref,
    }])
    with pytest.raises(ValueError, match="archive"):
        await preflight(bundle, "u_attacker")
    assert list(_sessions_root(tmp_home).glob("nx-import-*")) == []
    assert len(boundary_lines()) == 1


async def test_clean_bundle_still_passes(isolated_db, tmp_home, tmp_path, boundary_lines):
    bundle = _bundle(
        tmp_path,
        artifacts=[
            {"artifact_id": "art_1", "file_path": "README.md"},
            {"artifact_id": "art_2", "file_path": "/reports/q3/index.html"},  # legacy leading slash
            {"artifact_id": "art_3"},  # pointer-less legacy row
        ],
        skills=[{"name": "s1", "install_method": "zip", "agent_id": AID,
                 "archive_ref": f"skills/{AID}/s1.zip"}],
    )
    pf = await preflight(bundle, "u_ok")
    assert pf["preflight_token"]
    assert boundary_lines() == []


async def test_unreadable_artifacts_json_is_left_to_confirm(isolated_db, tmp_home, tmp_path):
    """Confirm already treats an unparseable artifacts.json as a warning (no
    artifact rows are written from it); preflight must not turn that into a
    hard failure — there is no path in it to act on."""
    path = tmp_path / "upload.nxbundle"
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("manifest.json", json.dumps({"bundle_format_version": "1.0", "agents": [AID]}))
        zf.writestr(f"agents/{AID}/agent.json", json.dumps({"agent_id": AID, "agent_name": "a"}))
        zf.writestr(f"agents/{AID}/artifacts.json", "{not json")
    pf = await preflight(path, "u_ok")
    assert pf["preflight_token"]
