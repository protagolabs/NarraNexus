"""
@file_name: test_confined_io_sites.py
@author: Bin Liang
@date: 2026-10-30
@description: Every host-side artifact read/write used to validate a path
(``confine_entry``: realpath under the owner's roots) and then RE-OPEN it by
string. A tenant can create symlinks in its own workspace, so a directory on
the path could be swapped for a link into another tenant's tree between the
check and the open. These tests perform exactly that swap AFTER the check (by
wrapping the checker) and assert the victim's bytes are never read, served,
hashed or overwritten, and that nothing is created in the victim's tree.

Each ``test_*_swap_*`` case goes red if the site is reverted to open the path
by string, because the swap lands the string path in the victim's directory.
The remaining cases pin that the owner's normal reads/writes (nested layout,
legacy flat layout, Range requests, HEAD) still work through the confined fd.
"""
from __future__ import annotations

import hashlib
import os
from datetime import datetime, timedelta, timezone

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from narranexus.platform.artifact import (
    ArtifactContentGone,
    ArtifactNotFound,
    ArtifactPathEscape,
    ArtifactService,
)
from narranexus.platform.artifact._artifact_impl import confinement, registration
from narranexus.platform.artifact._artifact_impl import freshness as freshness_mod
from narranexus.platform.artifact._artifact_impl import user_edit as user_edit_mod
from narranexus.platform.artifact._artifact_impl.commit import entry_size_bytes
from narranexus.platform.artifact._artifact_impl.freshness import office_lock_present
from narranexus.platform.repository.artifact_repository import ArtifactRepository
from narranexus.platform.schema import Artifact
from narranexus.platform.utils.workspace_paths import agent_workspace_relpath

OWNER, VICTIM = "user_owner", "user_victim"
A_OWN, A_VIC = "agent_own000000", "agent_vic000000"
SECRET = "victim-secret-bytes"
MINE = "owner-own-bytes"


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _row(path, kind="text/html", art_id="art_test0001", content_hash=None, updated=None):
    now = datetime.now(timezone.utc)
    return Artifact(
        artifact_id=art_id, agent_id=A_OWN, user_id=OWNER, session_id="s",
        title="t", kind=kind, file_path=path, size_bytes=1, content_hash=content_hash,
        created_at=updated or now, updated_at=updated or now,
    )


def _swap_dir_for_link(directory, victim_dir):
    """Replace ``directory`` (inside the owner's tree) with a link to the victim."""
    os.rename(directory, str(directory) + ".moved")
    os.symlink(victim_dir, directory)


async def _async_return(value):
    return value


@pytest.fixture
async def env(db_client, monkeypatch, tmp_path):
    base = tmp_path / "ws"
    base.mkdir()
    from narranexus.platform.settings import settings as sa
    monkeypatch.setattr(sa, "base_working_path", str(base), raising=False)
    monkeypatch.setattr(sa, "transcription_hmac_secret", "test-secret", raising=False)
    monkeypatch.setattr(sa, "admin_secret_key", "test-admin", raising=False)

    own_ws = base / agent_workspace_relpath(A_OWN, OWNER)
    vic_ws = base / agent_workspace_relpath(A_VIC, VICTIM)
    own = own_ws / "report"
    vic = vic_ws / "secret"
    own.mkdir(parents=True)
    vic.mkdir(parents=True)
    (own / "index.html").write_text(MINE, encoding="utf-8")
    (own / "style.css").write_text("body{}", encoding="utf-8")
    (vic / "index.html").write_text(SECRET, encoding="utf-8")
    (vic / "style.css").write_text("victim-css", encoding="utf-8")

    await db_client.insert("agents", {"agent_id": A_OWN, "agent_name": "t", "created_by": OWNER})
    repo = ArtifactRepository(db_client)
    return {
        "db": db_client, "repo": repo, "base": base, "own_ws": own_ws, "vic_ws": vic_ws,
        "own": own, "vic": vic, "svc": ArtifactService(db_client),
    }


def _swap_after_confine(monkeypatch, module, env, directory=None):
    """Wrap ``module.confine_entry`` so the owner's directory becomes a link to
    the victim's right AFTER the check passed — the TOCTOU window."""
    real = module.confine_entry
    fired = []

    def wrapper(*a, **k):
        out = real(*a, **k)
        if not fired:
            fired.append(True)
            _swap_dir_for_link(directory or env["own"], env["vic"])
        return out

    monkeypatch.setattr(module, "confine_entry", wrapper)
    return fired


# ── raw route ────────────────────────────────────────────────────────────────


@pytest.fixture
async def route(env, monkeypatch):
    from backend.routes.agents.artifacts import router as agents_router
    import backend.routes.agents.artifacts as agents_mod
    from backend.routes.artifacts.public import router as public_router
    import backend.routes.artifacts.public as public_mod

    monkeypatch.setattr(agents_mod, "get_db_client", lambda: _async_return(env["db"]))
    monkeypatch.setattr(public_mod, "get_db_client", lambda: _async_return(env["db"]))
    rel = agent_workspace_relpath(A_OWN, OWNER)
    await env["repo"].create(_row(f"{rel}/report/index.html"))
    app = FastAPI()
    app.include_router(agents_router, prefix="/api/agents")
    app.include_router(public_router, prefix="/api/public/artifacts")
    client = TestClient(app)

    def url(art_id="art_test0001", sub=""):
        tok = client.get(f"/api/agents/{A_OWN}/artifacts/{art_id}/view-token").json()["token"]
        return f"/api/public/artifacts/raw/{tok}/{sub}"

    return {**env, "client": client, "url": url, "app": app, "public_mod": public_mod}


def test_raw_serves_the_entry_through_the_confined_fd(route):
    r = route["client"].get(route["url"]())
    assert r.status_code == 200
    assert r.text == MINE
    assert r.headers["content-type"].startswith("text/html")
    assert r.headers["content-length"] == str(len(MINE))
    assert r.headers["accept-ranges"] == "bytes"
    assert r.headers["etag"] and r.headers["last-modified"]
    assert "frame-ancestors" in r.headers["content-security-policy"]
    assert r.headers["x-content-type-options"] == "nosniff"


def test_raw_serves_a_sibling_asset(route):
    r = route["client"].get(route["url"](sub="style.css"))
    assert r.status_code == 200
    assert r.text == "body{}"
    assert r.headers["content-type"].startswith("text/css")


def test_raw_head_is_headers_only(route):
    r = route["client"].head(route["url"]())
    assert r.status_code == 200
    assert r.content == b""
    assert r.headers["content-length"] == str(len(MINE))


def test_raw_single_range_request(route):
    r = route["client"].get(route["url"](), headers={"Range": "bytes=2-5"})
    assert r.status_code == 206
    assert r.content == MINE.encode()[2:6]
    assert r.headers["content-range"] == f"bytes 2-5/{len(MINE)}"
    assert r.headers["content-length"] == "4"


def test_raw_multi_range_request(route):
    r = route["client"].get(route["url"](), headers={"Range": "bytes=0-1,4-5"})
    assert r.status_code == 206
    assert r.headers["content-range"].startswith("multipart/byteranges")
    assert MINE.encode()[0:2] in r.content and MINE.encode()[4:6] in r.content


def test_raw_unsatisfiable_range_is_416(route):
    r = route["client"].get(route["url"](), headers={"Range": "bytes=999-1000"})
    assert r.status_code == 416


def test_raw_serves_a_large_file_in_full(route):
    big = os.urandom(300 * 1024)
    (route["own"] / "big.bin").write_bytes(big)
    r = route["client"].get(route["url"](sub="big.bin"))
    assert r.status_code == 200
    assert r.content == big


def test_raw_still_serves_the_legacy_flat_layout(route):
    flat = route["base"] / f"{A_OWN}_{OWNER}" / "old"
    flat.mkdir(parents=True)
    (flat / "index.html").write_text("legacy-bytes", encoding="utf-8")
    import asyncio
    asyncio.get_event_loop().run_until_complete(
        route["repo"].create(_row(f"{A_OWN}_{OWNER}/old/index.html", art_id="art_legacy001"))
    )
    r = route["client"].get(route["url"]("art_legacy001"))
    assert r.status_code == 200
    assert r.text == "legacy-bytes"


def test_raw_swap_after_resolve_never_serves_the_victim(route, monkeypatch):
    """Between resolve_raw_file and the response, the report/ directory becomes
    a link to the victim's secret/. FileResponse(path=...) followed it."""
    real = ArtifactService.resolve_raw_file

    async def swapping(self, **kw):
        out = await real(self, **kw)
        _swap_dir_for_link(route["own"], route["vic"])
        return out

    monkeypatch.setattr(ArtifactService, "resolve_raw_file", swapping)
    r = route["client"].get(route["url"]())
    assert r.status_code in (404, 410)
    assert SECRET not in r.text


def test_raw_swap_after_resolve_never_serves_the_victim_edit_bridge_path(route, monkeypatch):
    real = ArtifactService.resolve_raw_file

    async def swapping(self, **kw):
        out = await real(self, **kw)
        _swap_dir_for_link(route["own"], route["vic"])
        return out

    monkeypatch.setattr(ArtifactService, "resolve_raw_file", swapping)
    r = route["client"].get(route["url"]() + "?edit_bridge=1")
    assert r.status_code in (404, 410)
    assert SECRET not in r.text


def test_raw_swap_of_an_asset_directory_never_serves_the_victim(route, monkeypatch):
    sub = route["own"] / "assets"
    sub.mkdir()
    (sub / "a.css").write_text("owner-css", encoding="utf-8")
    vsub = route["vic"] / "assets"
    vsub.mkdir()
    (vsub / "a.css").write_text("victim-css-secret", encoding="utf-8")
    real = ArtifactService.resolve_raw_file

    async def swapping(self, **kw):
        out = await real(self, **kw)
        _swap_dir_for_link(sub, vsub)
        return out

    monkeypatch.setattr(ArtifactService, "resolve_raw_file", swapping)
    r = route["client"].get(route["url"](sub="assets/a.css"))
    assert r.status_code in (404, 410)
    assert "victim-css-secret" not in r.text


def test_raw_symlinked_leaf_is_not_served(route):
    os.symlink(route["vic"] / "index.html", route["own"] / "leak.html")
    r = route["client"].get(route["url"](sub="leak.html"))
    # realpath lands the target in the victim's tree, outside the artifact root
    assert r.status_code == 404
    assert SECRET not in r.text


def test_raw_file_object_is_closed_after_the_response(route):
    before = len(os.listdir("/proc/self/fd")) if os.path.isdir("/proc/self/fd") else None
    if before is None:
        pytest.skip("needs /proc/self/fd")
    for _ in range(5):
        route["client"].get(route["url"]())
        route["client"].head(route["url"]())
        route["client"].get(route["url"](), headers={"Range": "bytes=0-2"})
    assert len(os.listdir("/proc/self/fd")) <= before + 2  # client sockets only


# ── save_user_content ────────────────────────────────────────────────────────


async def test_save_swap_after_confine_never_writes_into_the_victims_file(env, monkeypatch):
    rel = agent_workspace_relpath(A_OWN, OWNER)
    await env["repo"].create(_row(f"{rel}/report/index.html"))
    _swap_after_confine(monkeypatch, user_edit_mod, env)
    with pytest.raises(ArtifactNotFound):
        await user_edit_mod.save_user_content(
            env["db"], agent_id=A_OWN, artifact_id="art_test0001",
            content="PWNED", base_hash=_sha(SECRET),
        )
    assert (env["vic"] / "index.html").read_text(encoding="utf-8") == SECRET
    assert sorted(os.listdir(env["vic"])) == ["index.html", "style.css"]  # no temp droppings


async def test_save_normal_edit_writes_through_the_confined_fd(env):
    rel = agent_workspace_relpath(A_OWN, OWNER)
    await env["repo"].create(_row(f"{rel}/report/index.html"))
    out = await user_edit_mod.save_user_content(
        env["db"], agent_id=A_OWN, artifact_id="art_test0001",
        content="new owner text", base_hash=_sha(MINE),
    )
    assert (env["own"] / "index.html").read_text(encoding="utf-8") == "new owner text"
    assert out.content_hash == _sha("new owner text")
    assert sorted(os.listdir(env["own"])) == ["index.html", "style.css"]


async def test_save_to_a_legacy_flat_entry_still_works(env):
    flat = env["base"] / f"{A_OWN}_{OWNER}" / "old"
    flat.mkdir(parents=True)
    (flat / "index.html").write_text("legacy", encoding="utf-8")
    await env["repo"].create(_row(f"{A_OWN}_{OWNER}/old/index.html"))
    await user_edit_mod.save_user_content(
        env["db"], agent_id=A_OWN, artifact_id="art_test0001",
        content="legacy-2", base_hash=_sha("legacy"),
    )
    assert (flat / "index.html").read_text(encoding="utf-8") == "legacy-2"


async def test_save_missing_entry_is_content_gone(env):
    rel = agent_workspace_relpath(A_OWN, OWNER)
    await env["repo"].create(_row(f"{rel}/report/index.html"))
    os.unlink(env["own"] / "index.html")
    with pytest.raises(ArtifactContentGone):
        await user_edit_mod.save_user_content(
            env["db"], agent_id=A_OWN, artifact_id="art_test0001",
            content="x", base_hash="h",
        )


async def test_save_stale_base_hash_still_conflicts(env):
    rel = agent_workspace_relpath(A_OWN, OWNER)
    await env["repo"].create(_row(f"{rel}/report/index.html"))
    from narranexus.platform.artifact import ArtifactEditConflict

    with pytest.raises(ArtifactEditConflict) as ei:
        await user_edit_mod.save_user_content(
            env["db"], agent_id=A_OWN, artifact_id="art_test0001",
            content="x", base_hash="stale",
        )
    assert ei.value.current_hash == _sha(MINE)


async def test_save_at_workspace_top_records_only_the_entry_size(env):
    """entry_size_bytes used to test dirname of a BASE-relative path, which is
    never empty, so an entry at the workspace top was charged the whole
    workspace (here: the 1 KB neighbour) instead of itself."""
    (env["own_ws"] / "junk.bin").write_bytes(b"j" * 1000)
    rel = agent_workspace_relpath(A_OWN, OWNER)
    (env["own_ws"] / "top.md").write_text("abc", encoding="utf-8")
    await env["repo"].create(_row(f"{rel}/top.md", kind="text/markdown"))
    out = await user_edit_mod.save_user_content(
        env["db"], agent_id=A_OWN, artifact_id="art_test0001",
        content="hello!", base_hash=_sha("abc"),
    )
    assert out.size_bytes == len("hello!")


async def test_save_in_a_dedicated_dir_records_the_directory_size(env):
    rel = agent_workspace_relpath(A_OWN, OWNER)
    await env["repo"].create(_row(f"{rel}/report/index.html"))
    out = await user_edit_mod.save_user_content(
        env["db"], agent_id=A_OWN, artifact_id="art_test0001",
        content="12345", base_hash=_sha(MINE),
    )
    assert out.size_bytes == 5 + len("body{}")


# ── office commit hashing ────────────────────────────────────────────────────


async def test_office_commit_swap_after_confine_never_hashes_the_victim(env, monkeypatch):
    rel = agent_workspace_relpath(A_OWN, OWNER)
    (env["own"] / "deck.pptx").write_bytes(b"pk-v1")
    (env["vic"] / "deck.pptx").write_bytes(b"victim-deck")
    await env["repo"].create(
        _row(f"{rel}/report/deck.pptx", kind="application/vnd.officecli-live",
             content_hash=hashlib.sha256(b"pk-v1").hexdigest())
    )
    _swap_after_confine(monkeypatch, user_edit_mod, env)
    with pytest.raises((ArtifactNotFound, ArtifactContentGone)):
        await user_edit_mod.commit_office_user_edit(env["db"], agent_id=A_OWN, artifact_id="art_test0001")
    row = await env["repo"].get_by_id("art_test0001")
    assert row.content_hash == hashlib.sha256(b"pk-v1").hexdigest()


async def test_office_commit_normal_path_hashes_the_owners_file(env):
    rel = agent_workspace_relpath(A_OWN, OWNER)
    (env["own"] / "deck.pptx").write_bytes(b"pk-v2")
    await env["repo"].create(
        _row(f"{rel}/report/deck.pptx", kind="application/vnd.officecli-live",
             content_hash=hashlib.sha256(b"pk-v1").hexdigest())
    )
    out = await user_edit_mod.commit_office_user_edit(env["db"], agent_id=A_OWN, artifact_id="art_test0001")
    assert out.content_hash == hashlib.sha256(b"pk-v2").hexdigest()


# ── freshness / hashing ──────────────────────────────────────────────────────


async def test_freshness_swap_after_confine_reads_nothing_of_the_victim(env, monkeypatch):
    rel = agent_workspace_relpath(A_OWN, OWNER)
    past = datetime.now(timezone.utc) - timedelta(hours=1)
    row = _row(f"{rel}/report/index.html", content_hash=_sha(MINE), updated=past)
    await env["repo"].create(row)
    _swap_after_confine(monkeypatch, freshness_mod, env)
    assert await freshness_mod.refresh_external_state(env["db"], row) == "missing"
    after = await env["repo"].get_by_id("art_test0001")
    assert after.content_hash == _sha(MINE)
    rows = await env["db"].execute(
        "SELECT action FROM instance_artifact_history WHERE artifact_id = %s",
        params=("art_test0001",), fetch=True)
    assert rows == []


async def test_freshness_detects_a_real_external_edit_through_the_confined_fd(env):
    rel = agent_workspace_relpath(A_OWN, OWNER)
    past = datetime.now(timezone.utc) - timedelta(hours=1)
    row = _row(f"{rel}/report/index.html", content_hash=_sha("older"), updated=past)
    await env["repo"].create(row)
    assert await freshness_mod.refresh_external_state(env["db"], row) == "external"
    after = await env["repo"].get_by_id("art_test0001")
    assert after.content_hash == _sha(MINE)


def test_compute_entry_hash_does_not_follow_a_swapped_directory(env):
    entry = str((env["own"] / "index.html").resolve())
    assert registration.compute_entry_hash(entry) == _sha(MINE)
    _swap_dir_for_link(env["own"], env["vic"])
    assert registration.compute_entry_hash(entry) is None


def test_compute_entry_hash_of_a_path_outside_the_base_is_none(env, tmp_path):
    stray = tmp_path / "stray.txt"
    stray.write_text("x")
    assert registration.compute_entry_hash(str(stray)) is None


def test_office_lock_probe_does_not_peek_through_a_swapped_directory(env):
    (env["vic"] / "~$index.html").write_text("lock", encoding="utf-8")
    entry = str((env["own"] / "index.html").resolve())
    assert office_lock_present(entry) is False
    _swap_dir_for_link(env["own"], env["vic"])
    assert office_lock_present(entry) is False  # old code: True (peeked into the victim)


def test_office_lock_probe_still_sees_the_owners_lock(env):
    (env["own"] / "~$index.html").write_text("lock", encoding="utf-8")
    assert office_lock_present(str((env["own"] / "index.html").resolve())) is True


# ── sizes ────────────────────────────────────────────────────────────────────


def test_dir_size_counts_regular_files_only_and_skips_symlinks(env, tmp_path):
    big = tmp_path / "bigfile"
    big.write_bytes(b"x" * 5000)
    os.symlink(big, env["own"] / "biglink")
    os.symlink(env["vic"], env["own"] / "dirlink")
    expected = len(MINE) + len("body{}")
    assert registration._dir_size(str(env["own"].resolve())) == expected


def test_entry_size_bytes_top_level_entry_counts_the_file_alone(env):
    (env["own_ws"] / "junk.bin").write_bytes(b"j" * 1000)
    (env["own_ws"] / "top.md").write_text("abc", encoding="utf-8")
    rel = agent_workspace_relpath(A_OWN, OWNER)
    art = _row(f"{rel}/top.md", kind="text/markdown")
    assert entry_size_bytes(str((env["own_ws"] / "top.md").resolve()), art) == 3


def test_entry_size_bytes_nested_entry_counts_the_directory(env):
    rel = agent_workspace_relpath(A_OWN, OWNER)
    art = _row(f"{rel}/report/index.html")
    assert entry_size_bytes(str((env["own"] / "index.html").resolve()), art) == len(MINE) + len("body{}")


def test_entry_size_bytes_team_folder_entry_counts_the_file_alone(env):
    from narranexus.platform.utils.workspace_paths import team_shared_dir

    tdir = team_shared_dir(OWNER, "team_0123456789ab", base=str(env["base"].resolve()))
    tdir.mkdir(parents=True)
    (tdir / "other.bin").write_bytes(b"o" * 700)
    (tdir / "doc.md").write_text("12", encoding="utf-8")
    art = _row(os.path.relpath(tdir / "doc.md", env["base"].resolve()), kind="text/markdown")
    art = art.model_copy(update={"team_id": "team_0123456789ab"})
    assert entry_size_bytes(str((tdir / "doc.md").resolve()), art) == 2


async def test_register_size_ignores_a_symlink_to_a_big_outside_file(env, tmp_path):
    big = tmp_path / "bigfile"
    big.write_bytes(b"x" * 5000)
    os.symlink(big, env["own"] / "biglink")
    rel = agent_workspace_relpath(A_OWN, OWNER)
    res = await env["svc"].register(
        agent_id=A_OWN, user_id=OWNER, session_id=None, kind="text/html",
        entry_path="report/index.html", title="t", description=None, target_artifact_id=None,
    )
    row = await env["repo"].get_by_id(res.artifact_id)
    assert row.size_bytes == len(MINE) + len("body{}")
    assert row.file_path == f"{rel}/report/index.html"


# ── office-asset upload ──────────────────────────────────────────────────────


@pytest.fixture
async def office(route, monkeypatch):
    rel = agent_workspace_relpath(A_OWN, OWNER)
    (route["own"] / "deck.pptx").write_bytes(b"pk-v1")
    await route["repo"].create(
        _row(f"{rel}/report/deck.pptx", kind="application/vnd.officecli-live", art_id="art_deck0001")
    )
    return route


def test_asset_upload_swap_after_resolve_never_lands_in_the_victims_dir(office, monkeypatch):
    real = ArtifactService.resolve_owned_entry

    def swapping(self, artifact):
        out = real(self, artifact)
        _swap_dir_for_link(office["own"], office["vic"])
        return out

    monkeypatch.setattr(ArtifactService, "resolve_owned_entry", swapping)
    r = office["client"].post(
        f"/api/agents/{A_OWN}/artifacts/art_deck0001/office-asset",
        files={"file": ("logo.png", b"PNGBYTES", "image/png")},
    )
    assert r.status_code in (404, 410)
    assert sorted(os.listdir(office["vic"])) == ["index.html", "style.css"]


def test_asset_upload_normal_lands_next_to_the_entry(office):
    r = office["client"].post(
        f"/api/agents/{A_OWN}/artifacts/art_deck0001/office-asset",
        files={"file": ("logo.png", b"PNGBYTES", "image/png")},
    )
    assert r.status_code == 200, r.text
    p = r.json()["path"]
    assert os.path.dirname(p) == str(office["own"].resolve())
    assert open(p, "rb").read() == b"PNGBYTES"


def test_asset_upload_oversize_leaves_no_partial_file(office, monkeypatch):
    import backend.routes.agents.artifacts as agents_mod

    monkeypatch.setattr(agents_mod, "MAX_OFFICE_ASSET_BYTES", 4)
    before = sorted(os.listdir(office["own"]))
    r = office["client"].post(
        f"/api/agents/{A_OWN}/artifacts/art_deck0001/office-asset",
        files={"file": ("logo.png", b"0123456789", "image/png")},
    )
    assert r.status_code == 413
    assert sorted(os.listdir(office["own"])) == before


# ── URL tabs ─────────────────────────────────────────────────────────────────


@pytest.fixture
async def url_env(env, monkeypatch):
    import narranexus.platform.artifact._artifact_impl.url_artifact as ua
    from narranexus.platform.schema.artifact_schema import EmbedVerdict

    async def fake_probe(url, *, our_scheme, resolver=None, client=None):
        return EmbedVerdict(recommended="iframe", reason="no-blocking-headers", probe_status="ok")

    async def fake_assert(url, *, resolver=None):
        return ["93.184.216.34"]

    async def fake_page_text(url, *, resolver=None, client=None):
        return "page text"

    monkeypatch.setattr(ua, "probe_url", fake_probe)
    monkeypatch.setattr(ua, "assert_public_http_url", fake_assert)
    monkeypatch.setattr(ua, "fetch_page_text", fake_page_text)
    return {**env, "ua": ua}


async def test_open_url_refuses_a_symlinked_tabs_dir_and_creates_nothing_in_the_victim(url_env):
    victim_tabs = url_env["vic_ws"] / "tabs"
    victim_tabs.mkdir()
    os.symlink(victim_tabs, url_env["own_ws"] / "tabs")
    with pytest.raises(ArtifactPathEscape):
        await url_env["svc"].open_url(
            agent_id=A_OWN, user_id=OWNER, session_id=None, url="https://example.org/x",
        )
    assert os.listdir(victim_tabs) == []


async def test_open_url_refuses_a_symlinked_workspace_and_creates_nothing_in_the_victim(url_env):
    shutil_target = url_env["vic_ws"]
    import shutil

    shutil.rmtree(url_env["own_ws"])
    os.symlink(shutil_target, url_env["own_ws"])
    before = sorted(os.listdir(shutil_target))
    with pytest.raises(ArtifactPathEscape):
        await url_env["svc"].open_url(
            agent_id=A_OWN, user_id=OWNER, session_id=None, url="https://example.org/x",
        )
    assert sorted(os.listdir(shutil_target)) == before


async def test_open_url_normal_path_writes_doc_and_content_as_real_files(url_env):
    res = await url_env["svc"].open_url(
        agent_id=A_OWN, user_id=OWNER, session_id=None, url="https://example.org/x",
    )
    row = await url_env["repo"].get_by_id(res.artifact_id)
    d = (url_env["base"] / row.file_path).parent
    assert sorted(os.listdir(d)) == ["content.md", "page.url.json"]
    assert not any(os.path.islink(d / n) for n in os.listdir(d))


async def test_set_embed_mode_swap_after_confine_never_rewrites_the_victims_doc(url_env, monkeypatch):
    res = await url_env["svc"].open_url(
        agent_id=A_OWN, user_id=OWNER, session_id=None, url="https://example.org/x",
    )
    row = await url_env["repo"].get_by_id(res.artifact_id)
    tab_dir = (url_env["base"] / row.file_path).parent
    victim_tab = url_env["vic_ws"] / "tabs" / "tabvictim"
    victim_tab.mkdir(parents=True)
    victim_doc = victim_tab / "page.url.json"
    victim_doc.write_text('{"url": "https://victim.example/", "title": "victim"}', encoding="utf-8")
    original = victim_doc.read_text(encoding="utf-8")

    import narranexus.platform.artifact._artifact_impl.confinement as conf

    real = conf.confine_entry
    fired = []

    def wrapper(*a, **k):
        out = real(*a, **k)
        if not fired:
            fired.append(True)
            _swap_dir_for_link(tab_dir, victim_tab)
        return out

    monkeypatch.setattr(conf, "confine_entry", wrapper)
    with pytest.raises(ArtifactNotFound):
        await url_env["svc"].set_embed_mode(agent_id=A_OWN, artifact_id=res.artifact_id, mode="newtab")
    assert victim_doc.read_text(encoding="utf-8") == original
    assert sorted(os.listdir(victim_tab)) == ["page.url.json"]


async def test_set_embed_mode_normal_path_rewrites_the_doc(url_env):
    import json

    res = await url_env["svc"].open_url(
        agent_id=A_OWN, user_id=OWNER, session_id=None, url="https://example.org/x",
    )
    await url_env["svc"].set_embed_mode(agent_id=A_OWN, artifact_id=res.artifact_id, mode="newtab")
    row = await url_env["repo"].get_by_id(res.artifact_id)
    doc = json.loads((url_env["base"] / row.file_path).read_text(encoding="utf-8"))
    assert doc["embed"]["user_override"] == "newtab"
    assert sorted(os.listdir((url_env["base"] / row.file_path).parent)) == ["content.md", "page.url.json"]
