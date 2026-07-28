"""
@file_name: test_resume_decision.py
@author:
@date: 2026-07-28
@description: step_3 resume decision (`_resolve_resume_session_id`) — the
four-fold validation gate in front of `--resume` (R2). Contract under test:
ONLY a full match (kill-switch on + handle exists + narrative + fingerprint
+ working path all agree) injects the stored cli_session_id; every other
outcome — including a lookup exception — is fail-open None => cold start.
"""
from types import SimpleNamespace

import pytest

from xyz_agent_context.agent_runtime._agent_runtime_steps.step_3_agent_loop import (
    _resolve_resume_session_id,
)
from xyz_agent_context.settings import settings

AGENT = "agent_r2_test"
FPRINT = "0123456789abcdef"
CWD = "/data/workspaces/u1/agent_r2_test"


class _FakeDb:
    """Just enough AsyncDatabaseClient surface for CliSessionRepository.get."""

    def __init__(self, row=None, raise_exc: Exception | None = None):
        self.row = row
        self.raise_exc = raise_exc
        self.calls: list = []

    async def get_one(self, table, filters):
        self.calls.append((table, filters))
        if self.raise_exc is not None:
            raise self.raise_exc
        return self.row


def _session() -> SimpleNamespace:
    return SimpleNamespace(session_id="sess_abc12345", current_narrative_id="nar_1")


def _row(**overrides) -> dict:
    base = dict(
        agent_id=AGENT,
        platform_session_id="sess_abc12345",
        framework="claude_code",
        cli_session_id="cli_session_xyz",
        config_fingerprint=FPRINT,
        working_path=CWD,
        narrative_id="nar_1",
    )
    base.update(overrides)
    return base


async def _decide(db, *, session=None, fingerprint=FPRINT, working_path=CWD):
    return await _resolve_resume_session_id(
        agent_id=AGENT,
        session=_session() if session is None else session,
        framework="claude_code",
        config_fingerprint=fingerprint,
        working_path=working_path,
        db_client=db,
    )


@pytest.mark.asyncio
async def test_all_anchors_match_injects_stored_handle():
    db = _FakeDb(row=_row())
    assert await _decide(db) == "cli_session_xyz"
    # The lookup used the canonical key triple.
    assert db.calls == [
        (
            "agent_cli_sessions",
            {
                "agent_id": AGENT,
                "platform_session_id": "sess_abc12345",
                "framework": "claude_code",
            },
        )
    ]


@pytest.mark.asyncio
async def test_flag_off_returns_none_without_touching_db(monkeypatch):
    monkeypatch.setattr(settings, "agent_loop_resume_enabled", False)
    db = _FakeDb(row=_row())
    assert await _decide(db) is None
    assert db.calls == []  # kill-switch short-circuits before any lookup


@pytest.mark.asyncio
async def test_missing_session_returns_none():
    db = _FakeDb(row=_row())
    result = await _resolve_resume_session_id(
        agent_id=AGENT,
        session=None,
        framework="claude_code",
        config_fingerprint=FPRINT,
        working_path=CWD,
        db_client=db,
    )
    assert result is None
    assert db.calls == []


@pytest.mark.asyncio
async def test_fingerprint_unavailable_returns_none():
    # step_3's fail-open fingerprint computation handed us None — without it
    # the stored handle cannot be validated, so no resume.
    db = _FakeDb(row=_row())
    assert await _decide(db, fingerprint=None) is None
    assert db.calls == []


@pytest.mark.asyncio
async def test_no_handle_returns_none():
    db = _FakeDb(row=None)
    assert await _decide(db) is None


@pytest.mark.asyncio
async def test_narrative_changed_returns_none():
    # Narrative switch = new CLI session BY RULE (topic domain changed).
    db = _FakeDb(row=_row(narrative_id="nar_OLD"))
    assert await _decide(db) is None


@pytest.mark.asyncio
async def test_fingerprint_mismatch_returns_none():
    # Provider / model / auth-kind / config-dir change => cold start.
    db = _FakeDb(row=_row(config_fingerprint="fedcba9876543210"))
    assert await _decide(db) is None


@pytest.mark.asyncio
async def test_working_path_changed_returns_none():
    # Session jsonl archives under the launch-cwd slug; a moved workspace
    # means --resume would look in the wrong place.
    db = _FakeDb(row=_row(working_path="/somewhere/else"))
    assert await _decide(db) is None


@pytest.mark.asyncio
async def test_lookup_error_fails_open_to_cold_start():
    db = _FakeDb(raise_exc=RuntimeError("db is down"))
    assert await _decide(db) is None  # never let the optimization kill a turn
