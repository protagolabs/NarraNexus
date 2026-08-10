"""
@file_name: _ship.py
@author:
@date: 2026-08-10
@description: Network log sink — the PUSH half of the observability
design (todo §C 决策 2).

``setup_logging`` registers this as one more loguru sink when the
deployer's env opts in; the file sink stays the source of truth and this
ships a copy to the collector (scripts/diag_collector). Levels:

  NEXUS_DIAG_SHIP=off   (or unset) — never registered. Local / self-
                        hosted deployments ship nothing, ever.
  NEXUS_DIAG_SHIP=meta  — AUDIT (25) and up: structured lifecycle
                        events + warnings/errors, no INFO bodies.
  NEXUS_DIAG_SHIP=full  — everything the file sink sees.

The ship level is enforced at loguru dispatch (the sink is registered
WITH that minimum level), so filtered records cost nothing.

Delivery contract — never interfere with the process being observed:
- ``enqueue=True`` gives this sink its own queue + worker thread; the
  emitting coroutine never waits on the network, and a slow collector
  cannot back up the FILE sink (separate queue).
- Batches flush at ``_BATCH_MAX`` records or ``_FLUSH_INTERVAL_S``
  seconds, gzip-compressed. Send failures DROP the batch after a short
  timeout — the file sink still has every line; gaps are pulled via the
  diagnostics endpoints. Failures report via ``sys.stderr`` (never
  through loguru: a failing ship must not emit into itself).
- On registration, the tail of today's existing log file is shipped
  once with ``"backfill": true`` — the crash-tail catch-up, so lines
  written just before an unclean restart still reach the collector.

Identity: every record carries the envelope fields
``(env, runtime_id, host, service)`` merged flat — the collector
partitions by them, and on a sprite (single manyfold runtime) that
makes every line attributable without per-line agent binding.
"""
from __future__ import annotations

import gzip
import json
import os
import socket
import sys
import threading
import time
from pathlib import Path
from typing import Any, Optional

import httpx

# Test seam, mirroring integrations/manyfold_outbound.
_transport_for_tests: Optional[httpx.BaseTransport] = None

_BATCH_MAX = 200
_FLUSH_INTERVAL_S = 3.0
_SEND_TIMEOUT_S = 2.0
_BACKFILL_LINES = 200
_AUDIT_LEVEL_NO = 25


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
        self._buf: list[dict] = []
        self._lock = threading.Lock()
        self._last_flush = time.monotonic()
        self._fail_streak = 0
        # Periodic flusher so a quiet process still delivers its tail.
        # Daemon: never blocks interpreter shutdown.
        self._timer = threading.Thread(
            target=self._flush_loop, name=f"diag-ship-{service}", daemon=True
        )
        self._timer.start()

    # -- loguru entry point ------------------------------------------------

    def __call__(self, message: Any) -> None:
        try:
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
            self._push(entry)
        except Exception as e:  # noqa: BLE001 — a broken ship must stay silent
            sys.stderr.write(f"[diag-ship] record build failed: {e}\n")

    def _push(self, entry: dict) -> None:
        with self._lock:
            self._buf.append(entry)
            should_flush = len(self._buf) >= _BATCH_MAX
        if should_flush:
            self.flush()

    def _flush_loop(self) -> None:
        while True:
            time.sleep(_FLUSH_INTERVAL_S)
            if time.monotonic() - self._last_flush >= _FLUSH_INTERVAL_S:
                self.flush()

    # -- delivery ----------------------------------------------------------

    def flush(self) -> None:
        with self._lock:
            if not self._buf:
                return
            batch, self._buf = self._buf, []
            self._last_flush = time.monotonic()
        self._send(batch)

    def _send(self, batch: list[dict]) -> None:
        body = gzip.compress(
            "\n".join(json.dumps(e, ensure_ascii=False) for e in batch).encode()
        )
        headers = {
            "Content-Type": "application/x-ndjson",
            "Content-Encoding": "gzip",
        }
        if self._config["token"]:
            headers["Authorization"] = f"Bearer {self._config['token']}"
        try:
            with httpx.Client(
                timeout=_SEND_TIMEOUT_S, transport=_transport_for_tests
            ) as client:
                resp = client.post(self._config["url"], content=body, headers=headers)
            if 200 <= resp.status_code < 300:
                self._fail_streak = 0
                return
            self._note_failure(f"HTTP {resp.status_code}")
        except Exception as e:  # noqa: BLE001 — drop the batch, file sink has it
            self._note_failure(f"{type(e).__name__}: {e}")

    def _note_failure(self, reason: str) -> None:
        self._fail_streak += 1
        # First failure and every 50th after: enough signal to notice a
        # dead collector without scrolling stderr off a cliff.
        if self._fail_streak == 1 or self._fail_streak % 50 == 0:
            sys.stderr.write(
                f"[diag-ship] send failed ({reason}); "
                f"streak={self._fail_streak}, batch dropped "
                f"(file log intact; recover via diagnostics pull)\n"
            )

    # -- crash-tail backfill ----------------------------------------------

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
                }
                for line in lines
            ]
            self._send(batch)
        except Exception as e:  # noqa: BLE001
            sys.stderr.write(f"[diag-ship] backfill failed: {e}\n")


def ship_sink_level(mode: str) -> Any:
    """Loguru minimum level for the ship sink: meta = AUDIT-and-up."""
    return _AUDIT_LEVEL_NO if mode == "meta" else "INFO"
