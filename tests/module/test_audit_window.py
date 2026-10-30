"""
@file_name: test_audit_window.py
@author: Bin Liang
@date: 2026-10-30
@description: WindowedAuditCounter — the one implementation of "bounded
in-process counts, one aggregated audit row per window" shared by the MCP
tokenless measurement and the user_id override audit. Its bounds are
invariants of the helper, not of its callers.
"""
from __future__ import annotations

import pytest

from narranexus.platform.module_system._audit_window import OVERFLOW_KEY, WindowedAuditCounter


@pytest.fixture
def rows(monkeypatch):
    captured: list[dict] = []

    async def fake_get_db_client():
        return object()

    class FakeAuditRepo:
        def __init__(self, db):
            pass

        async def record(self, **kw):
            captured.append(kw)

    monkeypatch.setattr("narranexus.platform.utils.db.db_factory.get_db_client", fake_get_db_client)
    monkeypatch.setattr(
        "narranexus.platform.repository.executor_audit_repository.ExecutorAuditRepository",
        FakeAuditRepo,
    )
    return captured


def _counter(**kw):
    return WindowedAuditCounter(
        event_type="ev", fields=("a", "b"), caps=kw.pop("caps", (4, 8)), **kw
    )


def test_note_truncates_each_dimension_to_its_cap():
    c = _counter()
    c.note(("abcdefgh", "0123456789"))
    assert c.pending() == {("abcd", "01234567"): 1}


def test_overflow_collapses_every_dimension_into_one_constant_key():
    c = _counter(keys_max=3)
    for i in range(10):
        c.note((f"k{i}", "x"))
    pending = c.pending()
    assert len(pending) == 4
    assert pending[OVERFLOW_KEY] == 7


def test_caps_must_match_fields():
    with pytest.raises(ValueError):
        WindowedAuditCounter(event_type="ev", fields=("a", "b"), caps=(4,))


async def test_flush_writes_one_row_per_window_and_returns_entries(rows):
    c = _counter()
    c.note(("u", "p"))
    entries = await c.flush()
    assert entries == [{"a": "u", "b": "p", "n": 1}]
    assert rows == [{"event_type": "ev", "detail": {"total": 1, "counts": entries}}]
    c.note(("u", "p"))
    c.note(("u", "p"))
    assert await c.flush() is None          # window still open
    assert c.pending() == {("u", "p"): 2}
    assert len(rows) == 1


async def test_flush_with_nothing_pending_is_free(rows):
    assert await _counter().flush() is None
    assert rows == []


async def test_flush_failure_is_swallowed_and_counts_are_gone(monkeypatch):
    async def boom():
        raise RuntimeError("db down")

    monkeypatch.setattr("narranexus.platform.utils.db.db_factory.get_db_client", boom)
    c = _counter()
    c.note(("u", "p"))
    entries = await c.flush()
    assert entries == [{"a": "u", "b": "p", "n": 1}]   # still returned for the caller's log line
    assert c.pending() == {}


def test_reset_clears_counts_and_window():
    c = _counter()
    c.note(("u", "p"))
    c.reset()
    assert c.pending() == {}
