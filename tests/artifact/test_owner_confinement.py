"""
@file_name: test_owner_confinement.py
@author: Bin Liang
@date: 2026-10-28
@description: An artifact row's ``file_path`` is user-influenced (bundle
import, LLM tool args). Serving, editing and uploading next to it must be
confined to the OWNER's roots (that agent's workspace candidates, plus the
team folder for a team artifact), not merely to ``base_working_path`` — the
latter is every tenant's root; a sibling agent of the same user is outside too.
Reduce ``confine_entry`` (artifact/_artifact_impl/confinement.py) or
``ArtifactService.resolve_owned_entry`` to a raw base join and the cross-user
cases go red. The symlinked-root cases are decided by the lexical-root
containment check (the realpath-ed entry lands in the victim's tree, outside
the lexical root); the explicit symlink refusal only sharpens the error.
"""
from __future__ import annotations

import os
import shutil
from datetime import datetime, timezone

import pytest

from narranexus.platform.artifact import ArtifactNotFound, ArtifactPathEscape, ArtifactService
from narranexus.platform.artifact._artifact_impl.confinement import confine_entry
from narranexus.platform.artifact._artifact_impl.user_edit import (
    commit_office_user_edit,
    save_user_content as save_user_edit,
)
from narranexus.platform.repository.artifact_repository import ArtifactRepository
from narranexus.platform.schema import Artifact
from narranexus.platform.utils.workspace_paths import agent_workspace_relpath, team_shared_dir

OWNER, VICTIM = "user_owner", "user_victim"
A_OWN, A_VIC = "agent_own000000", "agent_vic000000"


def _row(aid, uid, path, kind="text/html", art_id="art_test0001"):
    return Artifact(
        artifact_id=art_id, agent_id=aid, user_id=uid, session_id="s",
        title="t", kind=kind, file_path=path, size_bytes=1,
        created_at=datetime.now(timezone.utc), updated_at=datetime.now(timezone.utc),
    )


@pytest.fixture
async def env(db_client, monkeypatch, tmp_path):
    base = tmp_path / "ws"
    base.mkdir()
    from narranexus.platform.settings import settings as sa
    monkeypatch.setattr(sa, "base_working_path", str(base), raising=False)
    own = base / agent_workspace_relpath(A_OWN, OWNER) / "report"
    vic = base / agent_workspace_relpath(A_VIC, VICTIM) / "secret"
    own.mkdir(parents=True)
    vic.mkdir(parents=True)
    (own / "index.html").write_text("<p>mine</p>", encoding="utf-8")
    (vic / "index.html").write_text("<p>victim-secret</p>", encoding="utf-8")
    # Legacy flat layout dir for the owner: still a legitimate home.
    flat = base / f"{A_OWN}_{OWNER}" / "old"
    flat.mkdir(parents=True)
    (flat / "index.html").write_text("<p>legacy</p>", encoding="utf-8")
    repo = ArtifactRepository(db_client)
    return {"db": db_client, "repo": repo, "base": base, "vic": vic / "index.html", "flat": flat / "index.html"}


async def test_raw_refuses_entry_outside_owner_roots(env):
    # A row OWNED by the owner's agent whose pointer aims at the victim's file.
    await env["repo"].create(_row(A_OWN, OWNER, f"{VICTIM}/{A_VIC}/secret/index.html"))
    svc = ArtifactService(env["db"])
    with pytest.raises(ArtifactNotFound):
        await svc.resolve_raw_file(agent_id=A_OWN, artifact_id="art_test0001")


async def test_raw_still_serves_legacy_flat_layout(env):
    await env["repo"].create(_row(A_OWN, OWNER, f"{A_OWN}_{OWNER}/old/index.html"))
    svc = ArtifactService(env["db"])
    resolved = await svc.resolve_raw_file(agent_id=A_OWN, artifact_id="art_test0001")
    assert resolved.path == str(env["flat"].resolve())


async def test_edit_refuses_entry_outside_owner_roots(env):
    await env["repo"].create(_row(A_OWN, OWNER, f"{VICTIM}/{A_VIC}/secret/index.html"))
    before = env["vic"].read_text(encoding="utf-8")
    with pytest.raises(ArtifactNotFound):
        await save_user_edit(env["db"], agent_id=A_OWN, artifact_id="art_test0001", content="pwned", base_hash="x")
    assert env["vic"].read_text(encoding="utf-8") == before


async def test_symlinked_workspace_dir_is_refused(env):
    """The tenant can replace its own workspace dir (inside its bind mount) with
    a link to the victim's. Roots are lexical, the entry is realpath-ed, so the
    entry lands outside and the root itself is rejected as a link."""
    base = env["base"]
    own_ws = base / OWNER / A_OWN
    shutil.rmtree(own_ws)
    os.symlink(base / VICTIM / A_VIC, own_ws)
    with pytest.raises(ArtifactPathEscape):
        confine_entry(f"{OWNER}/{A_OWN}/secret/index.html", A_OWN, OWNER, str(base))


async def test_sibling_agent_of_same_user_is_outside_the_roots(env):
    base = env["base"]
    other = base / OWNER / "agent_other00000" / "r"
    other.mkdir(parents=True)
    (other / "index.html").write_text("x", encoding="utf-8")
    with pytest.raises(ArtifactPathEscape):
        confine_entry(f"{OWNER}/agent_other00000/r/index.html", A_OWN, OWNER, str(base))


async def test_team_artifact_lives_in_the_team_folder_only_with_a_team_id(env):
    base = env["base"]
    tdir = team_shared_dir(OWNER, "team_0123456789ab", base=str(base)) / "report"
    tdir.mkdir(parents=True)
    (tdir / "index.html").write_text("team", encoding="utf-8")
    rel = os.path.relpath(tdir / "index.html", base)
    assert confine_entry(rel, A_OWN, OWNER, str(base), team_id="team_0123456789ab") == str((tdir / "index.html").resolve())
    with pytest.raises(ArtifactPathEscape):
        confine_entry(rel, A_OWN, OWNER, str(base))


async def test_office_commit_refuses_entry_outside_owner_roots(env):
    await env["repo"].create(_row(A_OWN, OWNER, f"{VICTIM}/{A_VIC}/secret/index.html", kind="application/vnd.officecli-live"))
    with pytest.raises(ArtifactNotFound):
        await commit_office_user_edit(env["db"], agent_id=A_OWN, artifact_id="art_test0001")


async def test_registration_refuses_a_symlinked_workspace(env):
    base = env["base"]
    own_ws = base / OWNER / A_OWN
    shutil.rmtree(own_ws)
    os.symlink(base / VICTIM / A_VIC, own_ws)
    svc = ArtifactService(env["db"])
    with pytest.raises(ArtifactPathEscape):
        await svc.register(
            agent_id=A_OWN, user_id=OWNER, session_id=None, kind="text/html",
            entry_path="secret/index.html", title="t", description=None, target_artifact_id=None,
        )


async def test_resolve_owned_entry_refuses_another_users_file(env):
    """The public service entry the routes use: a row owned by the owner's agent
    whose pointer aims at the victim's file."""
    row = _row(A_OWN, OWNER, f"{VICTIM}/{A_VIC}/secret/index.html")
    with pytest.raises(ArtifactPathEscape):
        ArtifactService(env["db"]).resolve_owned_entry(row)


async def test_resolve_owned_entry_returns_the_owners_realpath(env):
    row = _row(A_OWN, OWNER, f"{OWNER}/{A_OWN}/report/index.html")
    got = ArtifactService(env["db"]).resolve_owned_entry(row)
    assert got == str((env["base"] / OWNER / A_OWN / "report" / "index.html").resolve())


async def test_team_registration_refuses_a_symlinked_team_folder(env):
    """The team folder sits in the owner's mount too; swapped for a link to the
    victim's team folder, registering an entry "inside" it must fail."""
    base = env["base"]
    team = "team_0123456789ab"
    victim_team = team_shared_dir(VICTIM, team, base=str(base)) / "report"
    victim_team.mkdir(parents=True)
    (victim_team / "index.html").write_text("victim-team", encoding="utf-8")
    own_team = team_shared_dir(OWNER, team, base=str(base))
    own_team.parent.mkdir(parents=True)
    os.symlink(victim_team.parent, own_team)
    svc = ArtifactService(env["db"])
    with pytest.raises(ArtifactPathEscape):
        await svc.register(
            agent_id=A_OWN, user_id=OWNER, session_id=None, kind="text/html",
            entry_path=str(own_team / "report" / "index.html"), title="t",
            description=None, target_artifact_id=None, team_id=team,
        )


async def test_registration_stores_a_clean_pointer_under_a_symlinked_base(env, tmp_path, monkeypatch):
    """The base itself may be a platform-level link (macOS /var). The stored
    pointer is relative to the REALPATH of the base, never ``../``-laden."""
    link = tmp_path / "ws_link"
    os.symlink(env["base"], link)
    from narranexus.platform.settings import settings as sa
    monkeypatch.setattr(sa, "base_working_path", str(link), raising=False)
    svc = ArtifactService(env["db"])
    res = await svc.register(
        agent_id=A_OWN, user_id=OWNER, session_id=None, kind="text/html",
        entry_path="report/index.html", title="t", description=None, target_artifact_id=None,
    )
    stored = (await env["repo"].get_by_id(res.artifact_id)).file_path
    assert stored == f"{OWNER}/{A_OWN}/report/index.html", stored
