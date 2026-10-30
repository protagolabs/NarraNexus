"""
@file_name: test_provisioning_confined.py
@author: Bin Liang
@date: 2026-10-30
@description: Platform-side provisioning / import / outbound-media code touched
the agent's tenant-writable workspace BY PATH. A directory swapped for (or a
file planted as) a link into another tenant's tree must make each of these
refuse — never write or read there — while the owner's honest case still works.

Covered here, each with a positive and a swapped-for-link negative case:
Bootstrap.md (apply_bootstrap), the welcome artifact (_create_welcome_artifact),
the CLI cwd (resolve_agent_workspace_cwd), the agent working-directory step
(ensure_agent_workspace_dir, step 3), the migration skill copy, Matrix media
reads (send_media_impl), the Arena credentials read-back (provision), the browser
evidence write (save_evidence), the team chat attachment size
(_sanitized_attachment) and the Bootstrap.md presence test used by the agent
list / update routes (bootstrap_file_present). The managed-ingress attachment
read lives in tests/backend/test_manyfold_im_ingress.py (it needs that file's
audit fixtures).
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest

from narranexus.platform.settings import settings
from narranexus.platform.utils.fs.workspace_paths import agent_workspace_path, agent_workspace_relpath

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


# ── welcome artifact ─────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_welcome_artifact_is_written_and_registered_for_the_owner(ws, db_client):
    from narranexus.platform.bootstrap.profiles import WelcomeArtifact, _create_welcome_artifact

    welcome = WelcomeArtifact(title="Welcome", html="<p>hi</p>")
    await _create_welcome_artifact(db_client, AGENT, USER, welcome, ws["own"])
    assert (ws["own"] / "welcome" / "index.html").read_text(encoding="utf-8") == "<p>hi</p>"
    from narranexus.platform.repository.artifact_repository import ArtifactRepository

    assert [a.title for a in await ArtifactRepository(db_client).list_pinned(AGENT)] == ["Welcome"]


@pytest.mark.asyncio
async def test_welcome_artifact_into_a_swapped_subdir_writes_nothing_in_the_victim(ws, db_client):
    from narranexus.platform.bootstrap.profiles import WelcomeArtifact, _create_welcome_artifact

    (ws["vic"] / "welcome").mkdir()
    os.symlink(ws["vic"] / "welcome", ws["own"] / "welcome")
    with pytest.raises(ValueError):
        await _create_welcome_artifact(db_client, AGENT, USER, WelcomeArtifact(title="W", html="<p>x</p>"), ws["own"])
    assert os.listdir(ws["vic"] / "welcome") == []


@pytest.mark.asyncio
async def test_welcome_artifact_link_at_the_file_name_is_not_written_through(ws, db_client):
    from narranexus.platform.bootstrap.profiles import WelcomeArtifact, _create_welcome_artifact

    (ws["own"] / "welcome").mkdir()
    victim_file = ws["vic"] / "precious.html"
    victim_file.write_bytes(SECRET)
    os.symlink(victim_file, ws["own"] / "welcome" / "index.html")
    await _create_welcome_artifact(db_client, AGENT, USER, WelcomeArtifact(title="W", html="<p>x</p>"), ws["own"])
    assert victim_file.read_bytes() == SECRET


# ── step 3: agent working directory ──────────────────────────────────────────


def test_agent_workspace_dir_is_created_idempotently_and_refuses_a_linked_one_with_a_clear_message(ws):
    import shutil

    from narranexus.platform.agent_runtime._agent_runtime_steps.step_3_agent_loop import ensure_agent_workspace_dir

    fresh = ws["base"] / agent_workspace_relpath("agent_fresh0000000", USER)
    ensure_agent_workspace_dir(str(fresh), str(ws["base"]))
    ensure_agent_workspace_dir(str(fresh), str(ws["base"]))
    assert fresh.is_dir()
    shutil.rmtree(ws["own"])
    os.symlink(ws["vic"], ws["own"])
    with pytest.raises(RuntimeError) as ei:
        ensure_agent_workspace_dir(str(ws["own"]), str(ws["base"]))
    assert "symbolic link" in str(ei.value) and "Replace the link" in str(ei.value)
    assert os.listdir(ws["vic"]) == []


def test_agent_workspace_dir_refuses_a_linked_user_dir(ws, tmp_path):
    from narranexus.platform.agent_runtime._agent_runtime_steps.step_3_agent_loop import ensure_agent_workspace_dir

    elsewhere = tmp_path / "other_disk"
    elsewhere.mkdir()
    os.symlink(elsewhere, ws["base"] / "u3")  # a desktop user's own `{base}/{user}` link
    with pytest.raises(RuntimeError, match="symbolic link"):
        ensure_agent_workspace_dir(str(ws["base"] / "u3" / "agent_x0000000000"), str(ws["base"]))
    assert os.listdir(elsewhere) == []


# ── Arena credentials read-back ──────────────────────────────────────────────


def _arena_service(monkeypatch, workspace_path):
    from types import SimpleNamespace

    from backend.integrations.arena import arena_provisioning_service as svc
    import narranexus.platform.repository.agent_repository as agent_repo_mod

    existing = SimpleNamespace(
        agent_id=AGENT, agent_name="Brave_Frost_Fox",
        agent_metadata={
            "provisioned_source": "arena", "arena_agent_id": "arena_1",
            "arena_agent_name": "Brave_Frost_Fox", "arena_owner_bind": "skipped_no_email",
        },
    )
    updates = {}

    class _Repo:
        def __init__(self, db):
            pass

        async def find(self, *a, **kw):
            return [existing]

        async def update_agent(self, agent_id, upd):
            updates["meta"] = upd["agent_metadata"]

    monkeypatch.setattr(agent_repo_mod, "AgentRepository", _Repo)
    import narranexus.platform.utils.fs.workspace_paths as wp_mod

    monkeypatch.setattr(wp_mod, "agent_workspace_path", lambda *a, **kw: workspace_path)
    seen = {}

    class _FakeOnboarder:
        def __init__(self, *, api_base, **kw):
            pass

        def bind_owner(self, creds, token):
            seen["key"] = creds.api_key
            return {"status": "bound", "email": "u@e.com"}

        def close(self):
            pass

    monkeypatch.setattr(svc, "ArenaOnboarder", _FakeOnboarder)
    return svc, seen, updates


def test_arena_credentials_are_read_back_from_the_owners_workspace(ws, monkeypatch):
    import asyncio
    import json

    skill = ws["own"] / "skills" / "arena"
    skill.mkdir(parents=True)
    (skill / "credentials.json").write_text(json.dumps({"api_key": "owner_key", "agent_id": "a"}), encoding="utf-8")
    svc, seen, updates = _arena_service(monkeypatch, ws["own"])
    result = asyncio.run(svc.ArenaProvisioningService(db_client=object()).provision("user_x", user_token="jwt"))
    assert seen["key"] == "owner_key" and result["owner_bind"] == "bound"


def test_arena_credentials_behind_a_swapped_skills_dir_are_not_read(ws, monkeypatch):
    import asyncio
    import json

    victim_skill = ws["vic"] / "skills" / "arena"
    victim_skill.mkdir(parents=True)
    (victim_skill / "credentials.json").write_text(json.dumps({"api_key": "VICTIM_KEY"}), encoding="utf-8")
    os.symlink(ws["vic"] / "skills", ws["own"] / "skills")
    svc, seen, updates = _arena_service(monkeypatch, ws["own"])
    result = asyncio.run(svc.ArenaProvisioningService(db_client=object()).provision("user_x", user_token="jwt"))
    assert "key" not in seen  # the victim's api_key never reached the binder
    assert result["owner_bind"] != "bound"


# ── browser evidence ─────────────────────────────────────────────────────────


def _evidence_call(monkeypatch, workspace):
    import base64
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    from narranexus_plugins.browser_module._browser_module_impl import evidence

    registered = {}

    class _Svc:
        def __init__(self, db):
            pass

        async def register(self, **kw):
            registered.update(kw)
            from datetime import datetime, timezone

            from narranexus.platform.schema import Artifact

            now = datetime.now(timezone.utc)
            return Artifact(
                artifact_id="art_evid0001", agent_id=AGENT, user_id=USER, session_id=None, title="E",
                kind="image/png", file_path="x", size_bytes=1, created_at=now, updated_at=now,
            )

    monkeypatch.setattr(evidence, "resolve_agent_workspace_cwd", AsyncMock(return_value=workspace))
    monkeypatch.setattr(evidence, "get_db_client", AsyncMock(return_value=object()))
    monkeypatch.setattr(evidence, "ArtifactService", _Svc)
    capture = {"mime_type": "image/png", "data": base64.b64encode(b"\x89PNG\r\n\x1a\nprivate").decode()}
    import asyncio

    return evidence, registered, lambda: asyncio.run(
        evidence.save_evidence(agent_id=AGENT, identity=SimpleNamespace(user_id=USER, event_id="e"), capture=capture, title="E")
    )


def test_browser_evidence_is_written_privately_for_the_owner(ws, monkeypatch):
    _, registered, call = _evidence_call(monkeypatch, ws["own"])
    call()
    entry = Path(registered["entry_path"])
    assert entry.read_bytes().endswith(b"private") and entry.parent.parent == ws["own"].resolve()
    assert os.stat(entry).st_mode & 0o777 == 0o600


def test_browser_evidence_into_a_swapped_workspace_writes_nothing_in_the_victim(ws, monkeypatch):
    import shutil

    shutil.rmtree(ws["own"])
    os.symlink(ws["vic"], ws["own"])
    _, registered, call = _evidence_call(monkeypatch, ws["own"])
    with pytest.raises(ValueError):
        call()
    assert os.listdir(ws["vic"]) == [] and not registered


def test_browser_evidence_cleanup_after_a_failed_registration_leaves_nothing(ws, monkeypatch):
    evidence, _, call = _evidence_call(monkeypatch, ws["own"])

    class _Boom:
        def __init__(self, db):
            pass

        async def register(self, **kw):
            raise ConnectionError("storage unavailable")

    monkeypatch.setattr(evidence, "ArtifactService", _Boom)
    with pytest.raises(ConnectionError):
        call()
    assert os.listdir(ws["own"]) == []


# ── team chat attachment size ────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_team_attachment_size_is_measured_through_the_confined_walk(ws, monkeypatch):
    from narranexus_plugins.teams import routes as team_routes
    from narranexus.platform.message_bus._bus_attachment_impl import store_bytes_into_bus

    att = await store_bytes_into_bus(
        user_id=USER, raw_bytes=b"12345", original_name="x.txt", mime_type="text/plain", base=str(ws["base"])
    )
    got = team_routes._sanitized_attachment(USER, {"rel_path": att["rel_path"]})
    assert got["size_bytes"] == 5

    # swapped between the lookup and the size read: nothing of the victim leaks
    vday = ws["base"] / V_USER / "_shared" / "bus_files" / os.path.basename(os.path.dirname(att["rel_path"]))
    vday.mkdir(parents=True)
    (vday / os.path.basename(att["rel_path"])).write_bytes(SECRET)
    real = team_routes.resolve_shared_file_for_user

    def swapping(user_id, rel_path, base=None):
        out = real(user_id, rel_path, base)
        bus = ws["base"] / USER / "_shared" / "bus_files"
        os.rename(bus, str(bus) + ".moved")
        os.symlink(ws["base"] / V_USER / "_shared" / "bus_files", bus)
        return out

    monkeypatch.setattr(team_routes, "resolve_shared_file_for_user", swapping)
    assert team_routes._sanitized_attachment(USER, {"rel_path": att["rel_path"]}) is None


# ── Bootstrap.md presence ────────────────────────────────────────────────────


def test_bootstrap_presence_reads_links_and_swapped_workspaces_as_absent(ws):
    import shutil

    from narranexus.platform.bootstrap.lifecycle import bootstrap_file_present

    path = str(ws["own"] / "Bootstrap.md")
    assert bootstrap_file_present(path) is False
    (ws["own"] / "Bootstrap.md").write_text("# b", encoding="utf-8")
    assert bootstrap_file_present(path) is True
    (ws["vic"] / "Bootstrap.md").write_text("# victim", encoding="utf-8")
    (ws["own"] / "Bootstrap.md").unlink()
    os.symlink(ws["vic"] / "Bootstrap.md", ws["own"] / "Bootstrap.md")
    assert bootstrap_file_present(path) is False  # a link is not a regular file here
    shutil.rmtree(ws["own"])
    os.symlink(ws["vic"], ws["own"])
    assert bootstrap_file_present(path) is False  # and a swapped workspace never shows the victim's
