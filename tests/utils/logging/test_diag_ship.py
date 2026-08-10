"""
@file_name: test_diag_ship.py
@author:
@date: 2026-08-10
@description: Network ship sink (observability push half) — env gating,
batch payload shape (gzip ndjson + envelope), drop-on-failure, backfill.
"""
from __future__ import annotations

import gzip
import json
from datetime import datetime, timezone
from types import SimpleNamespace

import httpx
import pytest

from xyz_agent_context.utils.logging import _ship


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    for key in (
        "NEXUS_DIAG_SHIP",
        "NEXUS_DIAG_SHIP_URL",
        "NEXUS_DIAG_SHIP_TOKEN",
        "NEXUS_DIAG_ENV",
        "MANYFOLD_RUNTIME_ID",
        "NARRA_SURFACE",
    ):
        monkeypatch.delenv(key, raising=False)


class _Recorder:
    def __init__(self, status: int = 200):
        self.requests: list[httpx.Request] = []
        self._status = status

    def transport(self) -> httpx.MockTransport:
        def _handle(request: httpx.Request) -> httpx.Response:
            self.requests.append(request)
            return httpx.Response(self._status, json={"ok": True})

        return httpx.MockTransport(_handle)

    def batches(self) -> list[list[dict]]:
        out = []
        for req in self.requests:
            body = gzip.decompress(req.content).decode()
            out.append([json.loads(ln) for ln in body.splitlines()])
        return out


def _message(text="hello", level="INFO", run_id="run1"):
    return SimpleNamespace(
        record={
            "time": datetime(2026, 8, 10, 12, 0, tzinfo=timezone.utc),
            "level": SimpleNamespace(name=level),
            "extra": {"run_id": run_id, "event_id": "evt1"},
            "name": "mod",
            "function": "fn",
            "line": 7,
            "message": text,
        }
    )


def _config(**over):
    base = {
        "mode": "full",
        "url": "https://collect.example.com/v1/ingest",
        "token": "tok-1",
        "env": "manyfold-staging",
        "runtime_id": "rt_x",
    }
    base.update(over)
    return base


class TestConfigGating:
    def test_off_by_default(self):
        assert _ship.ship_config() is None

    def test_mode_without_url_is_off(self, monkeypatch):
        monkeypatch.setenv("NEXUS_DIAG_SHIP", "full")
        assert _ship.ship_config() is None

    def test_unknown_mode_is_off(self, monkeypatch):
        monkeypatch.setenv("NEXUS_DIAG_SHIP", "everything")
        monkeypatch.setenv("NEXUS_DIAG_SHIP_URL", "https://c/v1/ingest")
        assert _ship.ship_config() is None

    def test_full_resolves_with_env_label_fallback(self, monkeypatch):
        monkeypatch.setenv("NEXUS_DIAG_SHIP", "full")
        monkeypatch.setenv("NEXUS_DIAG_SHIP_URL", "https://c/v1/ingest")
        monkeypatch.setenv("NARRA_SURFACE", "local")
        config = _ship.ship_config()
        assert config is not None
        assert config["env"] == "local"
        assert config["runtime_id"] == "-"

    def test_meta_level_is_audit_and_up(self):
        assert _ship.ship_sink_level("meta") == 25
        assert _ship.ship_sink_level("full") == "INFO"


class TestShipSink:
    def test_batch_carries_envelope_and_auth(self, monkeypatch):
        rec = _Recorder()
        monkeypatch.setattr(_ship, "_transport_for_tests", rec.transport())
        sink = _ship.ShipSink("backend", _config())
        sink(_message("line one"))
        sink(_message("line two", level="WARNING"))
        sink.flush()

        assert len(rec.requests) == 1
        req = rec.requests[0]
        assert req.headers["authorization"] == "Bearer tok-1"
        assert req.headers["content-encoding"] == "gzip"
        (batch,) = rec.batches()
        assert [e["message"] for e in batch] == ["line one", "line two"]
        first = batch[0]
        assert first["env"] == "manyfold-staging"
        assert first["runtime_id"] == "rt_x"
        assert first["service"] == "backend"
        assert first["run_id"] == "run1"
        assert first["level"] == "INFO"

    def test_size_triggered_flush(self, monkeypatch):
        rec = _Recorder()
        monkeypatch.setattr(_ship, "_transport_for_tests", rec.transport())
        sink = _ship.ShipSink("backend", _config())
        for i in range(_ship._BATCH_MAX):
            sink(_message(f"m{i}"))
        assert len(rec.requests) == 1  # flushed at the cap without waiting
        assert len(rec.batches()[0]) == _ship._BATCH_MAX

    def test_send_failure_drops_batch_and_never_raises(self, monkeypatch):
        rec = _Recorder(status=503)
        monkeypatch.setattr(_ship, "_transport_for_tests", rec.transport())
        sink = _ship.ShipSink("backend", _config())
        sink(_message("doomed"))
        sink.flush()  # must not raise
        assert len(rec.requests) == 1
        sink(_message("next"))
        sink.flush()
        # Buffer was not poisoned by the failure; next batch still sends.
        assert len(rec.requests) == 2

    def test_network_error_never_raises(self, monkeypatch):
        def _boom(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("down", request=request)

        monkeypatch.setattr(
            _ship, "_transport_for_tests", httpx.MockTransport(_boom)
        )
        sink = _ship.ShipSink("backend", _config())
        sink(_message("x"))
        sink.flush()  # silence is the contract

    def test_backfill_ships_tail_flagged(self, monkeypatch, tmp_path):
        rec = _Recorder()
        monkeypatch.setattr(_ship, "_transport_for_tests", rec.transport())
        log = tmp_path / "backend_20260810.log"
        log.write_text("\n".join(f"old line {i}" for i in range(300)))
        sink = _ship.ShipSink("backend", _config())
        sink.backfill_from(log)
        (batch,) = rec.batches()
        assert len(batch) == _ship._BACKFILL_LINES  # tail only
        assert batch[0]["backfill"] is True
        assert batch[-1]["message"] == "old line 299"

    def test_backfill_missing_file_is_noop(self, monkeypatch, tmp_path):
        rec = _Recorder()
        monkeypatch.setattr(_ship, "_transport_for_tests", rec.transport())
        sink = _ship.ShipSink("backend", _config())
        sink.backfill_from(tmp_path / "ghost.log")
        assert rec.requests == []


class TestAuditMirrorLine:
    @pytest.mark.asyncio
    async def test_audit_append_mirrors_one_log_line(self):
        """Every audit row emits one AUDIT-level log line so lifecycle
        events ride the push sink (the sink ships log records, not DB
        rows). Falls back to INFO when the custom level is absent."""
        from loguru import logger

        from xyz_agent_context.repository.channel_trigger_audit_repository import (
            ChannelTriggerAuditRepository,
        )

        class _Db:
            async def insert(self, table, row):
                pass

        captured: list = []
        handler_id = logger.add(
            lambda m: captured.append(m.record), level=0, format="{message}"
        )
        try:
            repo = ChannelTriggerAuditRepository("wechat", _Db())
            await repo.append(
                "managed_ingress_denied",
                agent_id="a1",
                message_id="m1",
                details={"reason": "nope"},
            )
        finally:
            logger.remove(handler_id)
        lines = [r for r in captured if "[audit]" in r["message"]]
        assert len(lines) == 1
        msg = lines[0]["message"]
        assert "channel=wechat" in msg
        assert "event=managed_ingress_denied" in msg
        assert "agent=a1" in msg
        assert lines[0]["level"].name in ("AUDIT", "INFO")
