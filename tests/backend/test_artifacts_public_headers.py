"""
Regression tests for the artifact public-raw response headers — making sure
the iframe-embedding fix (2026-05-27) doesn't get accidentally reverted.

Symptom this guards against: the dmg's artifact panel went white because
the response carried `X-Frame-Options: SAMEORIGIN` + `Cross-Origin-
Resource-Policy: same-origin`, both of which made the browser refuse to
render the iframe (the parent webview is `https://tauri.localhost` while
the artifact bytes come from `http://localhost:8000` — different origin).
Surgical replacement: `frame-ancestors {parent_origin}` in CSP, with
the parent origin detected from Referer/Origin per request.
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.routes.artifacts.public import (
    SAFE_HEADERS,
    _csp_for_html,
    _frame_ancestors,
    _non_html_csp,
)
from narranexus.platform.repository.artifact_repository import ArtifactRepository
from narranexus.platform.schema import Artifact
from narranexus.platform.utils.fs.workspace_paths import agent_workspace_relpath


def test_safe_headers_does_not_set_x_frame_options():
    # X-Frame-Options is the legacy all-or-nothing header that has no way
    # to express "the dmg's tauri.localhost is OK even though we serve
    # from localhost:8000". Setting it back will re-introduce the white-
    # screen P0.
    assert "X-Frame-Options" not in SAFE_HEADERS
    # Case-insensitive check (HTTP headers are case-insensitive).
    assert "x-frame-options" not in {k.lower() for k in SAFE_HEADERS}


def test_safe_headers_corp_is_cross_origin():
    # `same-origin` made the browser discard the response. Auth lives in
    # the HMAC token in the URL path, not in origin — anyone with the
    # token can read, so `cross-origin` is correct.
    assert SAFE_HEADERS["Cross-Origin-Resource-Policy"] == "cross-origin"


def test_csp_html_includes_frame_ancestors_with_parent_origin():
    origin = "https://tauri.localhost"
    csp = _csp_for_html(origin)
    assert f"frame-ancestors {_frame_ancestors(origin)}" in csp
    # And the script/style/etc. directives still pin to the request's host
    # (existing behaviour we're not regressing).
    assert f"script-src {origin}" in csp
    assert f"style-src {origin}" in csp
    assert "default-src 'none'" in csp


def test_csp_non_html_includes_frame_ancestors():
    # Every non-HTML kind (chart / csv / markdown / image / pdf) also gets
    # framed in the dmg webview, so they need the same allowance.
    for kind in [
        "application/vnd.echarts+json",
        "text/csv",
        "text/markdown",
        "image/png",
        "image/jpeg",
        "application/pdf",
    ]:
        csp = _non_html_csp(kind, "https://tauri.localhost")
        assert "frame-ancestors" in csp
        assert "https://tauri.localhost" in csp
        assert "http://127.0.0.1:*" in csp, (
            f"missing frame-ancestors in CSP for {kind!r}: {csp!r}"
        )


def test_csp_non_html_unknown_kind_falls_back_to_default_src_none_plus_frame_ancestors():
    # Unknown kinds shouldn't bypass the frame-ancestors allowance.
    csp = _non_html_csp("application/octet-stream", "https://tauri.localhost")
    assert "default-src 'none'" in csp
    assert "frame-ancestors" in csp
    assert "https://tauri.localhost" in csp


def test_csp_html_for_cloud_origin():
    # Sanity: works with a cloud-style origin too, not just tauri.localhost.
    csp = _csp_for_html("https://agent.narra.nexus")
    assert "frame-ancestors https://agent.narra.nexus" in csp
    assert "https://tauri.localhost" in csp
    assert "script-src https://agent.narra.nexus" in csp


# --- the sandbox directive, over the real route --------------------------------
# The iframe attribute only binds the frame our renderer builds. This URL is on
# the app origin, so a navigation or a redirect into a URL tab must find the
# sandbox in the response itself: GET and HEAD, entry and sibling, bridge or not.

OWNER = "user_owner"
AGENT = "agent_own000000"
ENTRY = "<html><head></head><body>artifact</body></html>"


def _directives(response) -> list[str]:
    return [d.strip() for d in response.headers["content-security-policy"].split(";")]


async def _async_return(value):
    return value


def _row(path, kind, art_id):
    now = datetime.now(timezone.utc)
    return Artifact(
        artifact_id=art_id, agent_id=AGENT, user_id=OWNER, session_id="s",
        title="t", kind=kind, file_path=path, size_bytes=1,
        created_at=now, updated_at=now,
    )


@pytest.fixture
async def raw(db_client, monkeypatch, tmp_path):
    base = tmp_path / "ws"
    from narranexus.platform.settings import settings as sa
    monkeypatch.setattr(sa, "base_working_path", str(base), raising=False)
    monkeypatch.setattr(sa, "transcription_hmac_secret", "test-secret", raising=False)
    monkeypatch.setattr(sa, "admin_secret_key", "test-admin", raising=False)

    rel = agent_workspace_relpath(AGENT, OWNER)
    report = base / rel / "report"
    report.mkdir(parents=True)
    (report / "index.html").write_text(ENTRY, encoding="utf-8")
    (report / "page.html").write_text("<script>opened()</script>", encoding="utf-8")
    (report / "style.css").write_text("body{}", encoding="utf-8")
    charts = base / rel / "charts"
    charts.mkdir()
    (charts / "a.png").write_bytes(b"\x89PNG\r\n")

    await db_client.insert("agents", {"agent_id": AGENT, "agent_name": "t", "created_by": OWNER})
    repo = ArtifactRepository(db_client)
    await repo.create(_row(f"{rel}/report/index.html", "text/html", "art_html0001"))
    await repo.create(_row(f"{rel}/charts/a.png", "image/png", "art_png00001"))

    from backend.routes.agents.artifacts import router as agents_router
    import backend.routes.agents.artifacts as agents_mod
    from backend.routes.artifacts.public import router as public_router
    import backend.routes.artifacts.public as public_mod
    monkeypatch.setattr(agents_mod, "get_db_client", lambda: _async_return(db_client))
    monkeypatch.setattr(public_mod, "get_db_client", lambda: _async_return(db_client))

    app = FastAPI()
    app.include_router(agents_router, prefix="/api/agents")
    app.include_router(public_router, prefix="/api/public/artifacts")
    client = TestClient(app)

    def url(art_id, sub=""):
        token = client.get(f"/api/agents/{AGENT}/artifacts/{art_id}/view-token").json()["token"]
        return f"/api/public/artifacts/raw/{token}/{sub}"

    return {"client": client, "url": url}


@pytest.mark.parametrize("method", ["GET", "HEAD"])
def test_html_entry_runs_sandboxed_and_cannot_post_a_form(raw, method):
    r = raw["client"].request(method, raw["url"]("art_html0001"))
    assert r.status_code == 200
    directives = _directives(r)
    assert directives[0] == "sandbox allow-scripts"
    assert "form-action 'none'" in directives
    assert "allow-same-origin" not in r.headers["content-security-policy"]
    assert r.headers["x-content-type-options"] == "nosniff"


@pytest.mark.parametrize("method", ["GET", "HEAD"])
def test_edit_bridge_entry_is_sandboxed_too(raw, method):
    r = raw["client"].request(method, raw["url"]("art_html0001") + "?edit_bridge=1")
    assert r.status_code == 200
    directives = _directives(r)
    assert directives[0] == "sandbox allow-scripts"
    assert "form-action 'none'" in directives
    assert "allow-same-origin" not in r.headers["content-security-policy"]
    if method == "GET":
        assert len(r.text) > len(ENTRY)  # the bridge was really injected


@pytest.mark.parametrize("method", ["GET", "HEAD"])
def test_non_html_entry_gets_a_tokenless_sandbox(raw, method):
    r = raw["client"].request(method, raw["url"]("art_png00001"))
    assert r.status_code == 200
    directives = _directives(r)
    assert directives[0] == "sandbox"
    assert any(d.startswith("frame-ancestors") for d in directives)


@pytest.mark.parametrize("method", ["GET", "HEAD"])
@pytest.mark.parametrize("sibling", ["page.html", "style.css"])
def test_sibling_gets_a_tokenless_sandbox(raw, method, sibling):
    r = raw["client"].request(method, raw["url"]("art_html0001", sibling))
    assert r.status_code == 200
    assert _directives(r)[:2] == ["sandbox", "default-src 'none'"]
