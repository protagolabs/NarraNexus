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
describe their fields and choose parameters, nothing else.

Invariants (the helper's, not its callers'):
- each key dimension is described ONCE by an ``AuditField``: its name, its
  length cap (str values only; None = not truncated, for server-side values
  like a port) and its overflow blank (the first field always renders
  "<overflow>", so only the others' blanks are used);
- past ``keys_max`` distinct keys everything folds into ONE constant key
  (``OVERFLOW_KEY``), whatever dimension varies — the dict is bounded at
  ``keys_max + 1`` entries. Callers only decide how that entry RENDERS (each
  field's blank keeps its type equal to the normal rows'), never the key;
- ``note`` is synchronous and does no I/O (it runs on tool hot paths and in a
  sync resolver). It raises only for a wiring error (a key of the wrong
  arity) — a programming bug that must surface, so it is not wrapped;
- ``flush`` writes at most once per window, snapshots and clears BEFORE the
  write (a concurrent note during the write lands in the next window), hands
  the entries to the caller's ``before_write`` callback BEFORE the DB write
  (a human log line is neither delayed by nor lost with a slow or cancelled
  write), and swallows callback and write failures at debug — a failure loses
  that window's line or counts, never the measured call.
"""
from __future__ import annotations

import time
from typing import Any, Callable, NamedTuple, Optional

from loguru import logger

OVERFLOW_KEY: tuple[str, ...] = ("<overflow>",)


class AuditField(NamedTuple):
    """One key dimension: name, str length cap (None = keep whole), and the
    value the overflow entry shows for it (match the field's normal type).
    The FIRST field always shows "<overflow>", so its blank is unused."""

    name: str
    cap: Optional[int] = None
    blank: Any = ""


class WindowedAuditCounter:
    """Aggregate keyed counts in-process; flush one audit row per window."""

    def __init__(
        self,
        *,
        event_type: str,
        fields: tuple[AuditField, ...],
        keys_max: int = 256,
        window_s: float = 60.0,
    ) -> None:
        if not fields:
            raise ValueError("at least one field")
        self._event_type = event_type
        self._fields = fields
        self._keys_max = keys_max
        self._window_s = window_s
        self._counts: dict[tuple, int] = {}
        self._deadline = 0.0  # first flush is immediate: "did it ever happen" is always answered

    def note(self, key: tuple) -> None:
        """Count one event. Sync, no I/O; raises only on a wrong-arity key."""
        if len(key) != len(self._fields):
            raise ValueError(f"key has {len(key)} parts, expected {len(self._fields)}")
        bounded = tuple(
            v[: f.cap] if isinstance(v, str) and f.cap is not None else v
            for v, f in zip(key, self._fields)
        )
        if bounded not in self._counts and len(self._counts) >= self._keys_max:
            bounded = OVERFLOW_KEY
        self._counts[bounded] = self._counts.get(bounded, 0) + 1

    async def flush(
        self, before_write: Optional[Callable[[list[dict[str, Any]]], None]] = None
    ) -> Optional[list[dict[str, Any]]]:
        """Write pending counts as one row if the window has elapsed.

        ``before_write`` (sync) receives the entries before the DB write; if it
        raises, the error is logged at debug and the write still happens.
        Returns the entries (a convenience), or None when nothing was due.
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
        # Normal keys in plain tuple order (same-position fields share a type);
        # the overflow key is placed last and never compared with them, so no
        # field type can make this sort raise outside the guarded write.
        entries = [
            self._entry(k, n)
            for k, n in sorted(counts.items(), key=lambda kv: (kv[0] == OVERFLOW_KEY, kv[0]))
        ]
        if before_write is not None:
            try:
                before_write(entries)
            except Exception as e:  # noqa: BLE001 — the observer must not break the observed
                logger.debug(f"[audit-window] {self._event_type} before_write failed: {e}")
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
                f.name: ("<overflow>" if i == 0 else f.blank) for i, f in enumerate(self._fields)
            }
        else:
            entry = {f.name: v for f, v in zip(self._fields, key)}
        entry["n"] = n
        return entry

    def pending(self) -> dict[tuple, int]:
        """A copy of the unflushed counts (tests, diagnostics)."""
        return dict(self._counts)

    def reset(self) -> None:
        """Drop pending counts and reopen the window (tests)."""
        self._counts.clear()
        self._deadline = 0.0
