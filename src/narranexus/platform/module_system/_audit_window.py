"""
@file_name: _audit_window.py
@author: Bin Liang
@date: 2026-10-30
@description: Bounded in-process counts, flushed as one aggregated audit row per window.

Two identity measurements need the same shape — mcp_auth's tokenless-call
measurement and _mcp_identity's user_id override audit: count events whose
key dimensions come (partly) from the caller, write ONE
``instance_executor_audit`` row per window with the aggregated counts, and
never let the measurement break the measured call. Written twice by hand, the
two copies had started to drift, and the subtle part (the overflow bucket
must collapse EVERY dimension, or the cap only holds for one of them — the
round-5 lesson) lived in two places. This is the one implementation; callers
choose only parameters.

Invariants (the helper's, not its callers'):
- each key dimension is truncated to its cap;
- past ``keys_max`` distinct keys everything folds into ONE constant key
  (``OVERFLOW_KEY``), whatever dimension varies — the dict is bounded at
  ``keys_max + 1`` entries;
- ``note`` is synchronous and does no I/O (it runs on tool hot paths and in a
  sync resolver; nothing in it can raise on valid input, so it is not wrapped);
- ``flush`` writes at most once per window, snapshots and clears BEFORE the
  write (a concurrent note during the write lands in the next window), and
  swallows write failures at debug — a failed or cancelled write loses that
  window's counts, never the call.
"""
from __future__ import annotations

import time
from typing import Any, Callable, Optional

from loguru import logger

OVERFLOW_KEY: tuple[str, ...] = ("<overflow>",)


class WindowedAuditCounter:
    """Aggregate keyed counts in-process; flush one audit row per window."""

    def __init__(
        self,
        *,
        event_type: str,
        fields: tuple[str, ...],
        caps: tuple[Optional[int], ...],
        blanks: Optional[tuple[Any, ...]] = None,
        keys_max: int = 256,
        window_s: float = 60.0,
    ) -> None:
        """
        Args:
            caps: per-field length cap for str values; None = not truncated
                (a server-side value such as a port).
            blanks: how the overflow entry renders the fields after the first
                (default all ""); only the presentation — the overflow KEY is
                always the one constant ``OVERFLOW_KEY``.
        """
        if len(fields) != len(caps):
            raise ValueError("one cap per field")
        if blanks is None:
            blanks = ("",) * len(fields)
        if len(blanks) != len(fields):
            raise ValueError("one blank per field")
        self._event_type = event_type
        self._fields = fields
        self._caps = caps
        self._blanks = blanks
        self._keys_max = keys_max
        self._window_s = window_s
        self._counts: dict[tuple, int] = {}
        self._deadline = 0.0  # first flush is immediate: "did it ever happen" is always answered

    def note(self, key: tuple) -> None:
        """Count one event. Sync, no I/O."""
        if len(key) != len(self._fields):
            raise ValueError(f"key has {len(key)} parts, expected {len(self._fields)}")
        bounded = tuple(
            v[:cap] if isinstance(v, str) and cap is not None else v
            for v, cap in zip(key, self._caps)
        )
        if bounded not in self._counts and len(self._counts) >= self._keys_max:
            bounded = OVERFLOW_KEY
        self._counts[bounded] = self._counts.get(bounded, 0) + 1

    async def flush(
        self, before_write: Optional[Callable[[list[dict[str, Any]]], None]] = None
    ) -> Optional[list[dict[str, Any]]]:
        """Write pending counts as one row if the window has elapsed.

        ``before_write`` (sync) receives the entries before the DB write, so a
        caller's human-facing log line is not delayed by, or lost with, a slow
        or cancelled write. Returns the entries, or None when nothing was due.
        A dict check when nothing is pending.
        """
        if not self._counts:
            return None
        now = time.monotonic()
        if now < self._deadline:
            return None
        self._deadline = now + self._window_s
        counts = dict(self._counts)
        self._counts.clear()
        # Plain tuple ordering: same-position fields share a type, and the
        # overflow key compares on its first (str) element.
        entries = [self._entry(k, n) for k, n in sorted(counts.items())]
        if before_write is not None:
            before_write(entries)
        try:
            from narranexus.platform.repository.executor_audit_repository import (
                ExecutorAuditRepository,
            )
            from narranexus.platform.utils.db.db_factory import get_db_client

            db = await get_db_client()
            await ExecutorAuditRepository(db).record(
                event_type=self._event_type,
                detail={"total": sum(counts.values()), "counts": entries},
            )
        except Exception as e:  # noqa: BLE001 — the observer must not break the observed
            logger.debug(f"[audit-window] {self._event_type} row not written: {e}")
        return entries

    def _entry(self, key: tuple, n: int) -> dict[str, Any]:
        if key == OVERFLOW_KEY:
            entry: dict[str, Any] = {
                f: ("<overflow>" if i == 0 else self._blanks[i]) for i, f in enumerate(self._fields)
            }
        else:
            entry = dict(zip(self._fields, key))
        entry["n"] = n
        return entry

    def pending(self) -> dict[tuple, int]:
        """A copy of the unflushed counts (tests, diagnostics)."""
        return dict(self._counts)

    def reset(self) -> None:
        """Drop pending counts and reopen the window (tests)."""
        self._counts.clear()
        self._deadline = 0.0
