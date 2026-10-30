"""
@file_name: test_tenant_file_routes_confined.py
@author: Bin Liang
@date: 2026-10-30
@description: The workspace file routes (download / upload / delete), the
attachment and inbox downloads, the public transcription audio route, the
Manyfold read/write gateway and the office-watch version poll all validated a
path and then re-opened it BY STRING. A tenant can plant symlinks in its own
tree, so a directory on the path could be swapped for a link into another
tenant's tree between the check and the open — leaking the victim's bytes /
metadata, or (delete, write) destroying / planting data in the victim's tree.

Every ``*_swap_*`` case wraps the route's resolver so the swap lands right
AFTER the check, then asserts the victim's tree is byte-for-byte untouched and
never served. They go red if the route is reverted to open the string path.
The ``ConfinedFileResponse`` cases pin header / Range / HEAD behaviour and that
the held fd is closed on every ending, including a client that goes away.
"""
from __future__ import annotations

import gc
import os

import httpx
import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from backend.routes._confined_response import ConfinedFileResponse, open_tenant_file
from narranexus.platform.utils.workspace_paths import agent_workspace_relpath

OWNER = "user_owner_000000000000000000000"
VICTIM = "user_victim_00000000000000000000"
A_OWN = "agent_0123456789ab"
A_VIC = "agent_ba9876543210"
SECRET = b"victim-secret-bytes"
AS_OWNER = {"X-User-Id": OWNER}


async def _async_return(value):
    return value


def _swap_dir_for_link(directory, victim_dir):
    os.rename(directory, str(directory) + ".moved")
    os.symlink(victim_dir, directory)


@pytest.fixture
async def env(db_client, monkeypatch, tmp_path):
    base = tmp_path / "workspaces"
    own_ws = base / agent_workspace_relpath(A_OWN, OWNER)
    vic_ws = base / agent_workspace_relpath(A_VIC, VICTIM)
    (own_ws / "report" / "sub").mkdir(parents=True)
    (vic_ws / "secret" / "sub").mkdir(parents=True)
    (own_ws / "report" / "a.txt").write_bytes(b"owner-a")
    (own_ws / "report" / "sub" / "x.txt").write_bytes(b"owner-x")
    (vic_ws / "secret" / "a.txt").write_bytes(SECRET)
    (vic_ws / "secret" / "sub" / "x.txt").write_bytes(SECRET)

    from narranexus.platform.settings import settings as sa
    monkeypatch.setattr(sa, "base_working_path", str(base), raising=False)

    import backend.routes._ownership as own_mod
    monkeypatch.setattr(own_mod, "get_db_client", lambda: _async_return(db_client))
    await db_client.insert("agents", {"agent_id": A_OWN, "agent_name": "mine", "created_by": OWNER})
    await db_client.insert("agents", {"agent_id": A_VIC, "agent_name": "theirs", "created_by": VICTIM})

    import backend.routes.agents.attachments as att_mod
    import backend.routes.agents.files as files_mod
    import backend.routes.inbox as inbox_mod

    app = FastAPI()

    @app.middleware("http")
    async def _identity(request: Request, call_next):
        request.state.user_id = request.headers.get("X-User-Id") or None
        return await call_next(request)

    app.include_router(files_mod.router, prefix="/api/agents")
    app.include_router(att_mod.router, prefix="/api/agents")
    app.include_router(inbox_mod.router, prefix="/api/inbox")
    return {
        "client": TestClient(app), "app": app, "base": base, "own_ws": own_ws, "vic_ws": vic_ws,
        "files_mod": files_mod, "att_mod": att_mod, "inbox_mod": inbox_mod,
    }


def _victim_untouched(env):
    assert (env["vic_ws"] / "secret" / "a.txt").read_bytes() == SECRET
    assert (env["vic_ws"] / "secret" / "sub" / "x.txt").read_bytes() == SECRET
    assert sorted(os.listdir(env["vic_ws"] / "secret")) == ["a.txt", "sub"]


# ── workspace file download ──────────────────────────────────────────────────


def test_download_serves_the_owners_file_with_attachment_headers(env):
    r = env["client"].get(f"/api/agents/{A_OWN}/files/raw", params={"path": "report/a.txt"}, headers=AS_OWNER)
    assert r.status_code == 200
    assert r.content == b"owner-a"
    assert 'filename="a.txt"' in r.headers["content-disposition"]
    assert r.headers["content-type"].startswith("text/plain")


def test_download_swap_after_resolve_never_serves_the_victim(env, monkeypatch):
    real = env["files_mod"]._resolve_within_workspace

    def swapping(workspace, rel):
        out = real(workspace, rel)
        _swap_dir_for_link(env["own_ws"] / "report", env["vic_ws"] / "secret")
        return out

    monkeypatch.setattr(env["files_mod"], "_resolve_within_workspace", swapping)
    r = env["client"].get(f"/api/agents/{A_OWN}/files/raw", params={"path": "report/a.txt"}, headers=AS_OWNER)
    assert r.status_code == 404
    assert SECRET not in r.content


def test_download_of_a_directory_is_404(env):
    r = env["client"].get(f"/api/agents/{A_OWN}/files/raw", params={"path": "report"}, headers=AS_OWNER)
    assert r.status_code == 404


# ── workspace file upload ────────────────────────────────────────────────────


def test_upload_writes_the_owners_file(env):
    r = env["client"].post(
        f"/api/agents/{A_OWN}/files", files={"file": ("note.txt", b"hello", "text/plain")}, headers=AS_OWNER
    )
    assert r.json()["success"] is True
    assert (env["own_ws"] / "note.txt").read_bytes() == b"hello"
    assert not any(n.startswith(".narra_tmp_") for n in os.listdir(env["own_ws"]))


def test_upload_replaces_an_existing_file(env):
    (env["own_ws"] / "note.txt").write_bytes(b"old")
    env["client"].post(
        f"/api/agents/{A_OWN}/files", files={"file": ("note.txt", b"new", "text/plain")}, headers=AS_OWNER
    )
    assert (env["own_ws"] / "note.txt").read_bytes() == b"new"


def test_upload_swap_of_the_workspace_never_writes_into_the_victim(env, monkeypatch):
    real = env["files_mod"]._get_workspace_path

    def swapping(agent_id, user_id):
        out = real(agent_id, user_id)
        _swap_dir_for_link(env["own_ws"], env["vic_ws"])
        return out

    monkeypatch.setattr(env["files_mod"], "_get_workspace_path", swapping)
    before = sorted(os.listdir(env["vic_ws"]))
    r = env["client"].post(
        f"/api/agents/{A_OWN}/files", files={"file": ("pwn.txt", b"pwn", "text/plain")}, headers=AS_OWNER
    )
    assert r.status_code == 404
    assert sorted(os.listdir(env["vic_ws"])) == before


def test_upload_over_a_planted_leaf_symlink_does_not_write_through_it(env):
    os.symlink(env["vic_ws"] / "secret" / "a.txt", env["own_ws"] / "note.txt")
    env["client"].post(
        f"/api/agents/{A_OWN}/files", files={"file": ("note.txt", b"mine", "text/plain")}, headers=AS_OWNER
    )
    _victim_untouched(env)


# ── workspace delete ─────────────────────────────────────────────────────────


def test_delete_removes_a_file_and_a_directory_tree(env):
    r = env["client"].delete(f"/api/agents/{A_OWN}/files/report/a.txt", headers=AS_OWNER)
    assert r.json() == {"success": True, "path": "report/a.txt", "error": None} or r.json()["success"] is True
    assert not (env["own_ws"] / "report" / "a.txt").exists()
    r = env["client"].delete(f"/api/agents/{A_OWN}/files/report", headers=AS_OWNER)
    assert r.json()["success"] is True
    assert not (env["own_ws"] / "report").exists()


def test_delete_missing_path_reports_not_found(env):
    r = env["client"].delete(f"/api/agents/{A_OWN}/files/report/nope.txt", headers=AS_OWNER)
    assert r.json()["success"] is False
    assert "not found" in r.json()["error"]


def test_delete_file_swap_of_an_intermediate_dir_never_deletes_the_victims_file(env, monkeypatch):
    real = env["files_mod"]._resolve_within_workspace

    def swapping(workspace, rel):
        out = real(workspace, rel)
        _swap_dir_for_link(env["own_ws"] / "report", env["vic_ws"] / "secret")
        return out

    monkeypatch.setattr(env["files_mod"], "_resolve_within_workspace", swapping)
    r = env["client"].delete(f"/api/agents/{A_OWN}/files/report/a.txt", headers=AS_OWNER)
    assert r.status_code == 404
    _victim_untouched(env)


def test_delete_tree_swap_of_an_intermediate_dir_never_deletes_the_victims_tree(env, monkeypatch):
    real = env["files_mod"]._resolve_within_workspace

    def swapping(workspace, rel):
        out = real(workspace, rel)
        _swap_dir_for_link(env["own_ws"] / "report", env["vic_ws"] / "secret")
        return out

    monkeypatch.setattr(env["files_mod"], "_resolve_within_workspace", swapping)
    r = env["client"].delete(f"/api/agents/{A_OWN}/files/report/sub", headers=AS_OWNER)
    assert r.status_code == 404
    _victim_untouched(env)


def test_delete_never_follows_a_link_inside_the_tree(env):
    os.symlink(env["vic_ws"] / "secret", env["own_ws"] / "report" / "sub" / "lnk")
    r = env["client"].delete(f"/api/agents/{A_OWN}/files/report", headers=AS_OWNER)
    assert r.json()["success"] is True
    assert not (env["own_ws"] / "report").exists()
    _victim_untouched(env)


# ── attachment download ──────────────────────────────────────────────────────


def _attachment_setup(env):
    up = env["own_ws"] / "user_upload_files" / "2026-10-29"
    up.mkdir(parents=True)
    (up / "pic.png").write_bytes(b"owner-png")
    vup = env["vic_ws"] / "user_upload_files" / "2026-10-29"
    vup.mkdir(parents=True)
    (vup / "pic.png").write_bytes(SECRET)
    return up / "pic.png"


def test_attachment_raw_serves_the_owners_file(env, monkeypatch):
    path = _attachment_setup(env)
    monkeypatch.setattr(env["att_mod"], "resolve_attachment_path", lambda a, u, f: path)
    r = env["client"].get(f"/api/agents/{A_OWN}/attachments/att_a1b2c3d4/raw", headers=AS_OWNER)
    assert r.status_code == 200
    assert r.content == b"owner-png"
    assert r.headers["content-type"] == "image/png"
    assert 'filename="pic.png"' in r.headers["content-disposition"]


def test_attachment_raw_swap_after_resolve_never_serves_the_victim(env, monkeypatch):
    path = _attachment_setup(env)

    def swapping(agent_id, user_id, file_id):
        _swap_dir_for_link(env["own_ws"] / "user_upload_files", env["vic_ws"] / "user_upload_files")
        return path

    monkeypatch.setattr(env["att_mod"], "resolve_attachment_path", swapping)
    r = env["client"].get(f"/api/agents/{A_OWN}/attachments/att_a1b2c3d4/raw", headers=AS_OWNER)
    assert r.status_code == 404
    assert SECRET not in r.content


# ── inbox attachment download ────────────────────────────────────────────────


def _inbox_setup(env, monkeypatch):
    shared = env["base"] / OWNER / "_shared" / "bus_files" / "2026-10-29"
    shared.mkdir(parents=True)
    (shared / "f.bin").write_bytes(b"owner-bus")
    vshared = env["base"] / VICTIM / "_shared" / "bus_files" / "2026-10-29"
    vshared.mkdir(parents=True)
    (vshared / "f.bin").write_bytes(SECRET)

    import backend.auth as auth_mod

    async def fake_user(request):
        return OWNER

    monkeypatch.setattr(auth_mod, "resolve_current_user_id", fake_user)
    return shared / "f.bin"


def test_inbox_attachment_raw_serves_the_owners_file(env, monkeypatch):
    _inbox_setup(env, monkeypatch)
    r = env["client"].get(
        "/api/inbox/attachments/raw", params={"path": f"{OWNER}/_shared/bus_files/2026-10-29/f.bin"}
    )
    assert r.status_code == 200
    assert r.content == b"owner-bus"


def test_inbox_attachment_raw_swap_after_resolve_never_serves_the_victim(env, monkeypatch):
    resolved = _inbox_setup(env, monkeypatch)
    import narranexus.platform.message_bus.attachments as att

    def swapping(user_id, rel_path, base=None):
        _swap_dir_for_link(env["base"] / OWNER / "_shared" / "bus_files", env["base"] / VICTIM / "_shared" / "bus_files")
        return resolved

    monkeypatch.setattr(att, "resolve_shared_file_for_user", swapping)
    r = env["client"].get(
        "/api/inbox/attachments/raw", params={"path": f"{OWNER}/_shared/bus_files/2026-10-29/f.bin"}
    )
    assert r.status_code == 404
    assert SECRET not in r.content


# ── public transcription audio ───────────────────────────────────────────────


def test_transcription_public_swap_after_resolve_never_serves_the_victim(env, monkeypatch):
    from narranexus.platform.agent_framework.llm.transcription import url_signer
    import backend.routes.transcription.public as public_route

    monkeypatch.setattr(url_signer.settings, "transcription_hmac_secret", "test-secret-32B")
    monkeypatch.setattr(url_signer.settings, "public_base_url", "https://my.host")
    path = _attachment_setup(env)

    def swapping(agent_id, user_id, file_id):
        _swap_dir_for_link(env["own_ws"] / "user_upload_files", env["vic_ws"] / "user_upload_files")
        return path

    monkeypatch.setattr(public_route, "resolve_attachment_path", swapping)
    app = FastAPI()
    app.include_router(public_route.router, prefix="/api/public/transcription")
    token = url_signer.mint(file_id="att_a1b2c3d4", agent_id=A_OWN, user_id=OWNER, variant="original")
    r = TestClient(app).get(f"/api/public/transcription/audio/{token}")
    assert r.status_code == 404
    assert SECRET not in r.content


# ── Manyfold gateway read / write ────────────────────────────────────────────


@pytest.fixture
def manyfold(env, monkeypatch):
    import backend.routes.manyfold.files as mf

    async def fake_root(agent_id):
        return env["own_ws"], OWNER

    monkeypatch.setattr(mf, "_resolve_workspace_root", fake_root)
    app = FastAPI()

    @app.middleware("http")
    async def _authed(request: Request, call_next):
        request.state.manyfold_authed = True
        return await call_next(request)

    app.include_router(mf.router)
    return {**env, "mf": mf, "mclient": TestClient(app)}


def test_manyfold_read_serves_the_owners_file(manyfold):
    r = manyfold["mclient"].get(f"/manyfold/agents/{A_OWN}/files/read", params={"path": "report/a.txt"})
    assert r.status_code == 200
    assert r.content == b"owner-a"
    assert r.headers["content-length"] == "7"


def test_manyfold_read_directory_is_400_and_missing_is_404(manyfold):
    assert manyfold["mclient"].get(f"/manyfold/agents/{A_OWN}/files/read", params={"path": "report"}).status_code == 400
    assert manyfold["mclient"].get(f"/manyfold/agents/{A_OWN}/files/read", params={"path": "nope"}).status_code == 404


def test_manyfold_read_swap_after_resolve_never_serves_the_victim(manyfold, monkeypatch):
    mf = manyfold["mf"]
    real = mf._safe_resolve

    def swapping(workspace, raw):
        out = real(workspace, raw)
        _swap_dir_for_link(manyfold["own_ws"] / "report", manyfold["vic_ws"] / "secret")
        return out

    monkeypatch.setattr(mf, "_safe_resolve", swapping)
    r = manyfold["mclient"].get(f"/manyfold/agents/{A_OWN}/files/read", params={"path": "report/a.txt"})
    assert r.status_code in (400, 404)
    assert SECRET not in r.content


def test_manyfold_write_creates_parents_and_file(manyfold):
    r = manyfold["mclient"].post(
        f"/manyfold/agents/{A_OWN}/files/write", params={"path": "inbox/x/y.bin"}, content=b"bytes"
    )
    assert r.status_code == 200, r.text
    assert (manyfold["own_ws"] / "inbox" / "x" / "y.bin").read_bytes() == b"bytes"


def test_manyfold_write_swap_after_resolve_never_writes_into_the_victim(manyfold, monkeypatch):
    mf = manyfold["mf"]
    real = mf._safe_resolve

    def swapping(workspace, raw):
        out = real(workspace, raw)
        _swap_dir_for_link(manyfold["own_ws"] / "report", manyfold["vic_ws"] / "secret")
        return out

    monkeypatch.setattr(mf, "_safe_resolve", swapping)
    r = manyfold["mclient"].post(
        f"/manyfold/agents/{A_OWN}/files/write", params={"path": "report/new.bin"}, content=b"pwn"
    )
    assert r.status_code == 403
    _victim_untouched(manyfold)


# ── office-watch version poll ────────────────────────────────────────────────


@pytest.fixture
def watch(env, monkeypatch):
    import backend.routes.office_watch.proxy as owp

    deck = env["own_ws"] / "report" / "deck.pptx"
    deck.write_bytes(b"pk-owner")
    (env["vic_ws"] / "secret" / "deck.pptx").write_bytes(b"pk-victim-much-longer")
    (env["vic_ws"] / "secret" / "~$deck.pptx").write_bytes(b"lock")

    async def fake_lookup(request, artifact_id):
        return (OWNER, A_OWN, str(deck), "report/deck.pptx")

    monkeypatch.setattr(owp, "_lookup_office_file", fake_lookup)
    monkeypatch.setattr(owp, "is_cloud_mode", lambda: False)
    app = FastAPI()
    app.include_router(owp.router, prefix="/api")
    return {**env, "wclient": TestClient(app), "deck": deck, "owp": owp}


def test_office_version_reports_the_owners_file(watch):
    r = watch["wclient"].get("/api/office-watch/version", params={"artifact_id": "art_x"})
    assert r.status_code == 200
    assert r.json()["size"] == len(b"pk-owner")
    assert r.json()["lock"] is False


def test_office_version_swap_never_reports_the_victims_size_or_lock(watch):
    _swap_dir_for_link(watch["own_ws"] / "report", watch["vic_ws"] / "secret")
    r = watch["wclient"].get("/api/office-watch/version", params={"artifact_id": "art_x"})
    assert r.status_code == 404
    assert "pk-victim" not in r.text


# ── ConfinedFileResponse ─────────────────────────────────────────────────────


def _scope(method="GET", headers=None):
    return {
        "type": "http", "method": method, "path": "/", "query_string": b"",
        "headers": [(k.lower().encode(), v.encode()) for k, v in (headers or {}).items()],
        "extensions": {"http.response.pathsend": {}},
    }


async def _noop_receive():
    return {"type": "http.disconnect"}


def _file(tmp_path, data=b"0123456789"):
    p = tmp_path / "f.bin"
    p.write_bytes(data)
    return open(p, "rb")


async def test_response_closes_the_file_after_a_normal_send(tmp_path):
    f = _file(tmp_path)
    sent = []

    async def send(msg):
        sent.append(msg)

    await ConfinedFileResponse(f, media_type="application/octet-stream")(_scope(), _noop_receive, send)
    assert f.closed
    assert b"".join(m.get("body", b"") for m in sent) == b"0123456789"
    # never delegates the label path to the server
    assert not any(m["type"] == "http.response.pathsend" for m in sent)


async def test_response_closes_the_file_for_head(tmp_path):
    f = _file(tmp_path)

    async def send(msg):
        pass

    await ConfinedFileResponse(f)(_scope("HEAD"), _noop_receive, send)
    assert f.closed


async def test_response_closes_the_file_when_the_client_disconnects(tmp_path):
    """send() raising = the client went away mid-stream."""
    f = _file(tmp_path, b"x" * (300 * 1024))
    calls = {"n": 0}

    async def send(msg):
        calls["n"] += 1
        if calls["n"] >= 2:
            raise OSError("client disconnected")

    with pytest.raises(OSError):
        await ConfinedFileResponse(f)(_scope(), _noop_receive, send)
    assert f.closed


async def test_response_closes_the_file_on_a_range_error(tmp_path):
    f = _file(tmp_path)
    sent = []

    async def send(msg):
        sent.append(msg)

    await ConfinedFileResponse(f)(_scope(headers={"Range": "bytes=50-60"}), _noop_receive, send)
    assert sent[0]["status"] == 416
    assert f.closed


def test_response_never_awaited_does_not_hold_the_fd(tmp_path):
    f = _file(tmp_path)
    resp = ConfinedFileResponse(f)
    assert not f.closed
    del resp
    gc.collect()
    assert f.closed


def test_response_construction_failure_closes_the_file(tmp_path, monkeypatch):
    f = _file(tmp_path)
    import backend.routes._confined_response as cr

    monkeypatch.setattr(cr.os, "fstat", lambda fd: (_ for _ in ()).throw(OSError("fstat failed")))
    with pytest.raises(OSError):
        ConfinedFileResponse(f)
    assert f.closed


async def test_response_sets_stat_headers_filename_and_ranges(tmp_path):
    f = _file(tmp_path)
    sent = []

    async def send(msg):
        sent.append(msg)

    await ConfinedFileResponse(f, media_type="text/plain", filename="a b.txt")(
        _scope(headers={"Range": "bytes=2-4"}), _noop_receive, send
    )
    headers = {k.decode(): v.decode() for k, v in sent[0]["headers"]}
    assert sent[0]["status"] == 206
    assert headers["content-range"] == "bytes 2-4/10"
    assert headers["content-length"] == "3"
    assert "filename*=utf-8''a%20b.txt" in headers["content-disposition"]
    assert b"".join(m.get("body", b"") for m in sent[1:]) == b"234"


def test_open_tenant_file_refuses_outside_the_base_and_links(env, tmp_path):
    outside = tmp_path / "elsewhere.txt"
    outside.write_bytes(b"x")
    assert open_tenant_file(outside) is None
    assert open_tenant_file(env["own_ws"] / "report" / "missing.txt") is None
    assert open_tenant_file(env["own_ws"] / "report") is None  # a directory
    os.symlink(env["vic_ws"] / "secret" / "a.txt", env["own_ws"] / "report" / "lnk")
    assert open_tenant_file(env["own_ws"] / "report" / "lnk") is None
    f = open_tenant_file(env["own_ws"] / "report" / "a.txt")
    assert f is not None
    with f:
        assert f.read() == b"owner-a"
