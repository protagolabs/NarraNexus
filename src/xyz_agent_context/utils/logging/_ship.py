"""
@file_name: _ship.py
@author:
@date: 2026-08-10
@description: Network log sink — the PUSH half of the observability
design (pull half = the manyfold diagnostics endpoints; the two share
no machinery on purpose).

``setup_logging`` registers this as one more loguru sink when the
deployer's env opts in; the file sink stays the source of truth and this
ships a copy to the collector (scripts/diag_collector). Levels:

  NEXUS_DIAG_SHIP=off   (or unset) — never registered. Local / self-
                        hosted deployments ship nothing, ever.
  NEXUS_DIAG_SHIP=meta  — AUDIT (25) and up: structured lifecycle
                        events + warnings/errors, no INFO bodies.
  NEXUS_DIAG_SHIP=full  — everything the file sink sees, at the file
                        sink's resolved level.

The ship level is enforced at loguru dispatch (the sink is registered
WITH that minimum level), so filtered records cost nothing.

Delivery contract — never interfere with the process being observed:
- ``enqueue=True`` gives this sink its own queue + worker thread; the
  emitting coroutine never waits on the network, and a slow collector
  cannot back up the FILE sink (separate queue).
- Batches flush at ``_BATCH_MAX`` records, ``_BATCH_MAX_BYTES``
  serialized bytes (half the collector's 8MB wire cap, gzip headroom —
  count-only batching can exceed a byte-capped receiver), or
  ``_FLUSH_INTERVAL_S`` seconds. Send failures DROP the batch after a
  short timeout — the file sink still has every line; gaps are pulled
  via the diagnostics endpoints. Failures report via ``sys.stderr``
  (never through loguru: a failing ship must not emit into itself).
- Circuit breaker: ``_BREAKER_THRESHOLD`` consecutive TRANSIENT
  failures OPEN it for ``_BREAKER_COOLDOWN_S`` — records drop at the
  door instead of buffering (a dead collector must not become a memory
  leak: the enqueue queue is unbounded while the drain rate is floored
  at timeout×batch). After the cooldown the breaker goes HALF-OPEN:
  traffic is admitted for one probe batch, whose failure re-opens
  IMMEDIATELY (no re-earning the threshold) and whose success closes
  fully. Permanent rejections (4xx: over-size, auth misconfig) drop
  the batch WITHOUT feeding the breaker — "this batch is unacceptable"
  is not "the collector is down".
- ONE module-level ``atexit`` handler flushes every live sink on clean
  exit (final shutdown otherwise loses the last interval forever;
  startup backfill only helps when the SAME service starts again).
  Sinks are tracked in a ``WeakSet`` — per-instance atexit handlers
  would pin every sink ever constructed and stack serial timeouts.
- On registration, the tail of today's existing log file is shipped
  once with ``"backfill": true`` — the crash-tail catch-up, so lines
  written just before an unclean restart still reach the collector.

Identity: every record carries the envelope fields
``(env, runtime_id, host, service)`` merged flat — the collector
partitions by them, and on a sprite (single manyfold runtime) that
makes every line attributable without per-line agent binding.
"""
from __future__ import annotations

import atexit
import gzip
import json
import os
import socket
import sys
import threading
import time
import weakref
from pathlib import Path
from typing import Any, Optional

import httpx

# Test seam, mirroring integrations/manyfold_outbound.
_transport_for_tests: Optional[httpx.BaseTransport] = None

_BATCH_MAX = 200
_BATCH_MAX_BYTES = 4 * 1024 * 1024
_FLUSH_INTERVAL_S = 3.0
_SEND_TIMEOUT_S = 2.0
_BACKFILL_LINES = 200
_AUDIT_LEVEL_NO = 25
_BREAKER_THRESHOLD = 5
_BREAKER_COOLDOWN_S = 60.0

_LIVE_SINKS: "weakref.WeakSet[ShipSink]" = weakref.WeakSet()


def _flush_all_at_exit() -> None:
    for sink in list(_LIVE_SINKS):
        try:
            sink.flush()
        except Exception:  # noqa: BLE001 — exiting; nothing left to report to
            pass


atexit.register(_flush_all_at_exit)


def ship_mode() -> str:
    mode = os.environ.get("NEXUS_DIAG_SHIP", "off").strip().lower()
    return mode if mode in ("meta", "full") else "off"


def ship_config() -> Optional[dict]:
    """Resolved sender config, or None when shipping is off/unconfigured."""
    mode = ship_mode()
    url = os.environ.get("NEXUS_DIAG_SHIP_URL", "").strip()
    if mode == "off" or not url:
        return None
    return {
        "mode": mode,
        "url": url,
        "token": os.environ.get("NEXUS_DIAG_SHIP_TOKEN", "").strip(),
        "env": (
            os.environ.get("NEXUS_DIAG_ENV", "").strip()
            or os.environ.get("NARRA_SURFACE", "").strip()
            or "unknown"
        ),
        "runtime_id": os.environ.get("MANYFOLD_RUNTIME_ID", "").strip() or "-",
    }


class ShipSink:
    """Loguru callable sink: buffer → batch → gzip POST. Never raises."""

    def __init__(self, service: str, config: dict) -> None:
        self._service = service
        self._config = config
        self._host = socket.gethostname()
        # Buffer holds SERIALIZED lines: the byte-based flush condition
        # then costs nothing extra (each record serializes exactly once).
        self._buf: list[str] = []
        self._buf_bytes = 0
        self._lock = threading.Lock()
        # Serializes _send: both the enqueue worker (size-triggered flush)
        # and the timer thread can reach it; without this the breaker
        # accounting races and two batches can interleave on the wire.
        self._send_lock = threading.Lock()
        self._last_flush = time.monotonic()
        self._fail_streak = 0
        # Breaker state: OPEN while now < _cooldown_until; HALF-OPEN once
        # the cooldown elapsed, until a probe batch settles it.
        self._cooldown_until = 0.0
        self._half_open = False
        self._dropped_in_cooldown = 0
        _LIVE_SINKS.add(self)
        # Periodic flusher so a quiet process still delivers its tail.
        # Daemon: never blocks interpreter shutdown.
        self._timer = threading.Thread(
            target=self._flush_loop, name=f"diag-ship-{service}", daemon=True
        )
        self._timer.start()

    # -- loguru entry point ------------------------------------------------

    def __call__(self, message: Any) -> None:
        try:
            if self._breaker_open():
                self._dropped_in_cooldown += 1
                return
            record = message.record
            entry = {
                "ts": record["time"].isoformat(),
                "level": record["level"].name,
                "service": self._service,
                "env": self._config["env"],
                "runtime_id": self._config["runtime_id"],
                "host": self._host,
                "run_id": str(record["extra"].get("run_id", "") or ""),
                "event_id": str(record["extra"].get("event_id", "") or ""),
                "logger": f"{record['name']}:{record['function']}:{record['line']}",
                "message": record["message"],
            }
            self._push(json.dumps(entry, ensure_ascii=False))
        except Exception as e:  # noqa: BLE001 — a broken ship must stay silent
            sys.stderr.write(f"[diag-ship] record build failed: {e}\n")

    def _push(self, line: str) -> None:
        with self._lock:
            self._buf.append(line)
            self._buf_bytes += len(line)
            should_flush = (
                len(self._buf) >= _BATCH_MAX
                or self._buf_bytes >= _BATCH_MAX_BYTES
            )
        if should_flush:
            self.flush()

    def _flush_loop(self) -> None:
        while True:
            time.sleep(_FLUSH_INTERVAL_S)
            if time.monotonic() - self._last_flush >= _FLUSH_INTERVAL_S:
                self.flush()

    # -- circuit breaker ---------------------------------------------------

    def _breaker_open(self) -> bool:
        if self._cooldown_until <= 0:
            return False
        if time.monotonic() < self._cooldown_until:
            return True
        # Cooldown elapsed → HALF-OPEN: admit traffic; the next probe
        # batch settles it (failure re-opens immediately, success closes).
        self._cooldown_until = 0.0
        self._half_open = True
        if self._dropped_in_cooldown:
            sys.stderr.write(
                f"[diag-ship] breaker half-open after cooldown; "
                f"{self._dropped_in_cooldown} records dropped while open "
                f"(file log intact)\n"
            )
            self._dropped_in_cooldown = 0
        return False

    def _open_breaker(self, reason: str) -> None:
        self._cooldown_until = time.monotonic() + _BREAKER_COOLDOWN_S
        self._half_open = False
        self._fail_streak = 0
        with self._lock:
            dropped = len(self._buf)
            self._buf.clear()
            self._buf_bytes = 0
        self._dropped_in_cooldown += dropped
        sys.stderr.write(
            f"[diag-ship] breaker OPEN ({reason}); dropping records for "
            f"{_BREAKER_COOLDOWN_S:.0f}s (file log intact; recover via "
            f"diagnostics pull)\n"
        )

    # -- delivery ----------------------------------------------------------

    def flush(self) -> None:
        # With the breaker open the door is already starving the buffer;
        # anything still here (e.g. the atexit sweep racing a fresh OPEN)
        # must not spend a timeout on a known-dead collector.
        if self._cooldown_until > 0 and time.monotonic() < self._cooldown_until:
            with self._lock:
                self._dropped_in_cooldown += len(self._buf)
                self._buf.clear()
                self._buf_bytes = 0
            return
        with self._lock:
            if not self._buf:
                return
            batch, self._buf = self._buf, []
            self._buf_bytes = 0
            self._last_flush = time.monotonic()
        self._send(batch)

    def _send(self, lines: list[str]) -> None:
        body = gzip.compress("\n".join(lines).encode())
        headers = {
            "Content-Type": "application/x-ndjson",
            "Content-Encoding": "gzip",
        }
        if self._config["token"]:
            headers["Authorization"] = f"Bearer {self._config['token']}"
        with self._send_lock:
            self._send_locked(body, headers)

    def _send_locked(self, body: bytes, headers: dict) -> None:
        try:
            with httpx.Client(
                timeout=_SEND_TIMEOUT_S, transport=_transport_for_tests
            ) as client:
                resp = client.post(self._config["url"], content=body, headers=headers)
            if 200 <= resp.status_code < 300:
                self._fail_streak = 0
                self._half_open = False
                return
            if 400 <= resp.status_code < 500:
                # Permanent rejection (413 over-size, 401 misconfig):
                # "this batch is unacceptable" is not "the collector is
                # down" — drop it, say so, do NOT feed the breaker.
                sys.stderr.write(
                    f"[diag-ship] batch rejected (HTTP {resp.status_code}); "
                    f"dropped without breaker accounting\n"
                )
                return
            self._note_transient_failure(f"HTTP {resp.status_code}")
        except Exception as e:  # noqa: BLE001 — drop the batch, file sink has it
            self._note_transient_failure(f"{type(e).__name__}: {e}")

    def _note_transient_failure(self, reason: str) -> None:
        if self._half_open:
            # The probe failed — straight back to OPEN.
            self._open_breaker(f"probe failed: {reason}")
            return
        self._fail_streak += 1
        if self._fail_streak >= _BREAKER_THRESHOLD:
            self._open_breaker(
                f"{self._fail_streak} consecutive failures ({reason})"
            )
            return
        # First failure and every 50th after: enough signal to notice a
        # dead collector without scrolling stderr off a cliff.
        if self._fail_streak == 1 or self._fail_streak % 50 == 0:
            sys.stderr.write(
                f"[diag-ship] send failed ({reason}); "
                f"streak={self._fail_streak}, batch dropped "
                f"(file log intact; recover via diagnostics pull)\n"
            )

    # -- crash-tail backfill ----------------------------------------------

    def backfill_async(self, log_file: Path) -> None:
        """Backfill on a daemon thread: the send is a synchronous POST
        with up to the full timeout — inline it would stall every process
        START whenever the collector is slow, which is exactly the
        interference this sink promises never to cause."""
        threading.Thread(
            target=self.backfill_from,
            args=(log_file,),
            name=f"diag-ship-backfill-{self._service}",
            daemon=True,
        ).start()

    def backfill_from(self, log_file: Path) -> None:
        """Ship the tail of an existing log file once, flagged backfill.

        Covers lines written just before an unclean restart (the previous
        process died with batches unflushed). Duplicate lines on the
        collector are possible and acceptable — dedup is a read-time
        concern, losing the crash context is not."""
        try:
            if not log_file.is_file():
                return
            lines = log_file.read_text(errors="replace").splitlines()[
                -_BACKFILL_LINES:
            ]
            if not lines:
                return
            batch = [
                json.dumps(
                    {
                        "ts": "",
                        "level": "BACKFILL",
                        "service": self._service,
                        "env": self._config["env"],
                        "runtime_id": self._config["runtime_id"],
                        "host": self._host,
                        "run_id": "",
                        "event_id": "",
                        "logger": "",
                        "message": line,
                        "backfill": True,
                    },
                    ensure_ascii=False,
                )
                for line in lines
            ]
            self._send(batch)
        except Exception as e:  # noqa: BLE001
            sys.stderr.write(f"[diag-ship] backfill failed: {e}\n")


def ship_sink_level(mode: str, resolved_level: str = "INFO") -> Any:
    """Loguru minimum level for the ship sink.

    meta = AUDIT-and-up. full = whatever the FILE sink runs at — the
    documented contract is "full ships what the file sink sees", and
    hardcoding INFO here broke exactly the case that matters most:
    NEXUS_LOG_LEVEL=DEBUG turned on for an incident showed DEBUG in the
    local file but never at the collector."""
    return _AUDIT_LEVEL_NO if mode == "meta" else resolved_level
