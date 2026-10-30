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

from narranexus.platform.module_system._audit_window import OVERFLOW_KEY, AuditField, WindowedAuditCounter


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
        event_type="ev", fields=(AuditField("a", cap=4), AuditField("b", cap=8)), **kw
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


def test_at_least_one_field():
    with pytest.raises(ValueError):
        WindowedAuditCounter(event_type="ev", fields=())


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


async def test_reset_clears_counts_and_reopens_the_window(rows):
    c = _counter()
    c.note(("u", "p"))
    await c.flush()                      # closes the window
    c.note(("u", "p"))
    c.reset()
    assert c.pending() == {}
    c.note(("v", "q"))
    assert await c.flush() is not None   # window reopened: written immediately
    assert len(rows) == 2


def test_none_cap_means_not_truncated():
    c = WindowedAuditCounter(event_type="ev", fields=(AuditField("a", cap=2), AuditField("b")))
    c.note(("abcdef", "0123456789"))
    assert c.pending() == {("ab", "0123456789"): 1}


def test_key_must_have_one_part_per_field():
    with pytest.raises(ValueError):
        _counter().note(("only-one",))


async def test_overflow_entry_shape_default_and_custom_blanks(rows):
    c = _counter(keys_max=1)
    c.note(("a", "x"))
    c.note(("b", "y"))
    entries = await c.flush()
    assert {"a": "<overflow>", "b": "", "n": 1} in entries
    c2 = WindowedAuditCounter(event_type="ev", fields=(AuditField("a", cap=4), AuditField("b", blank=0)),
                              keys_max=1)
    c2.note(("a", 1))
    c2.note(("b", 2))
    assert {"a": "<overflow>", "b": 0, "n": 1} in await c2.flush()


async def test_before_write_runs_before_the_db_write(monkeypatch):
    order: list[str] = []

    async def fake_get_db_client():
        order.append("db")
        return object()

    class FakeAuditRepo:
        def __init__(self, db):
            pass

        async def record(self, **kw):
            order.append("write")

    monkeypatch.setattr("narranexus.platform.utils.db.db_factory.get_db_client", fake_get_db_client)
    monkeypatch.setattr(
        "narranexus.platform.repository.executor_audit_repository.ExecutorAuditRepository",
        FakeAuditRepo,
    )
    c = _counter()
    c.note(("u", "p"))
    await c.flush(before_write=lambda entries: order.append(f"log:{len(entries)}"))
    assert order == ["log:1", "db", "write"]


async def test_a_failing_before_write_never_breaks_the_flush(rows):
    """The callback is the caller's; if it raises, the row is still written
    and nothing propagates to the measured call."""
    c = _counter()
    c.note(("u", "p"))

    def boom(_entries):
        raise RuntimeError("log formatting broke")

    entries = await c.flush(before_write=boom)
    assert entries == [{"a": "u", "b": "p", "n": 1}]
    assert len(rows) == 1
