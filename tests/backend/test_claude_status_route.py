"""
@file_name: test_claude_status_route.py
@date: 2026-09-09
@description: Tests for the get_claude_status route function's expiry check.

/codex-status already gained an expiry check (incident 2026-06-11): a
present credentials file is not proof a session works, so an expired
token must flip logged_in to False and surface expired=True. This mirror
route filled expires_at from the legacy credentials file but never
compared it against "now" (B-25 / GitHub #111) — this file covers that
gap using the same `_expiry_is_past` helper /codex-status already relies
on. We don't spin up a real FastAPI server — we call the route handler
directly with a mock Request, same pattern as test_codex_status_route.py.
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest


def _mock_request(role: str = "user") -> MagicMock:
    req = MagicMock()
    req.state.role = role
    return req


@pytest.mark.asyncio
async def test_expired_credentials_reports_not_logged_in(tmp_path, monkeypatch):
    """A present .credentials.json with an expiry in the PAST must report
    logged_in=False and expired=True — before this fix expires_at was
    filled but never compared, so an expired token still read as
    logged_in=True (GitHub #111)."""
    from backend.routes.providers import get_claude_status

    creds_dir = tmp_path / ".claude"
    creds_dir.mkdir()
    (creds_dir / ".credentials.json").write_text(
        '{"claudeAiOauth": {"accessToken": "tok", "expiresAt": "2020-01-01T00:00:00Z"}}'
    )
    monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path)
    monkeypatch.setenv("DATABASE_URL", "sqlite:///test.db")

    with patch("shutil.which", return_value="/usr/local/bin/claude"):
        resp = await get_claude_status(_mock_request())

    d = resp["data"]
    assert d["cli_installed"] is True
    assert d["logged_in"] is False
    assert d["expired"] is True


@pytest.mark.asyncio
async def test_valid_credentials_report_logged_in(tmp_path, monkeypatch):
    """A not-yet-expired credentials file must keep reporting logged_in=True
    and expired=False — the expiry check must not false-positive on a
    working session."""
    from backend.routes.providers import get_claude_status

    creds_dir = tmp_path / ".claude"
    creds_dir.mkdir()
    (creds_dir / ".credentials.json").write_text(
        '{"claudeAiOauth": {"accessToken": "tok", "expiresAt": "2099-01-01T00:00:00Z"}}'
    )
    monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path)
    monkeypatch.setenv("DATABASE_URL", "sqlite:///test.db")

    with patch("shutil.which", return_value="/usr/local/bin/claude"):
        resp = await get_claude_status(_mock_request())

    d = resp["data"]
    assert d["logged_in"] is True
    assert d["expired"] is False


@pytest.mark.asyncio
async def test_unparseable_expiry_fails_open_logged_in(tmp_path, monkeypatch):
    """An expiry we can't confidently parse must NOT be reported as
    expired — fail open, matching /codex-status's rule (under-warn rather
    than wrongly lock out a working session)."""
    from backend.routes.providers import get_claude_status

    creds_dir = tmp_path / ".claude"
    creds_dir.mkdir()
    (creds_dir / ".credentials.json").write_text(
        '{"claudeAiOauth": {"accessToken": "tok", "expiresAt": "sometime-soon"}}'
    )
    monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path)
    monkeypatch.setenv("DATABASE_URL", "sqlite:///test.db")

    with patch("shutil.which", return_value="/usr/local/bin/claude"):
        resp = await get_claude_status(_mock_request())

    d = resp["data"]
    assert d["logged_in"] is True
    assert d["expired"] is False
