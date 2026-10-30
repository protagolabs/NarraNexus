"""
@file_name: test_confined_response_headers.py
@author: Bin Liang
@date: 2026-10-30
@description: ConfinedFileResponse never lets a browser sniff tenant bytes.

Every route that serves a tenant file goes through this class, so the default
lives here: a file uploaded as text/plain must not be reinterpreted as HTML on
the app origin. The header is merged under the caller's, never over it.
"""
from __future__ import annotations

from pathlib import Path

import pytest
from starlette.applications import Starlette
from starlette.routing import Route
from starlette.testclient import TestClient

from backend.routes._confined_response import ConfinedFileResponse


def _client(path: Path, headers=None) -> TestClient:
    async def serve(_request):
        return ConfinedFileResponse(open(path, "rb"), media_type="text/plain", headers=headers)

    return TestClient(Starlette(routes=[Route("/f", serve, methods=["GET", "HEAD"])]))


@pytest.fixture
def tenant_file(tmp_path) -> Path:
    path = tmp_path / "upload.txt"
    path.write_text("<script>not html</script>", encoding="utf-8")
    return path


@pytest.mark.parametrize("method", ["GET", "HEAD"])
def test_nosniff_is_the_default(tenant_file, method):
    r = _client(tenant_file).request(method, "/f")
    assert r.status_code == 200
    assert r.headers["x-content-type-options"] == "nosniff"


def test_caller_headers_survive_the_merge(tenant_file):
    r = _client(tenant_file, {"Content-Security-Policy": "sandbox"}).get("/f")
    assert r.headers["content-security-policy"] == "sandbox"
    assert r.headers["x-content-type-options"] == "nosniff"


@pytest.mark.parametrize("name", ["X-Content-Type-Options", "x-content-type-options"])
def test_a_caller_value_wins_in_any_spelling_and_is_not_doubled(tenant_file, name):
    r = _client(tenant_file, {name: "caller"}).get("/f")
    assert r.headers.get_list("x-content-type-options") == ["caller"]
