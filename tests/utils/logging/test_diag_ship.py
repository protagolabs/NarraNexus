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
def _clean_env(monkeypatch, tmp_path):
    for key in (
        "NEXUS_DIAG_SHIP",
        "NEXUS_DIAG_SHIP_URL",
        "NEXUS_DIAG_SHIP_TOKEN",
        "NEXUS_DIAG_ENV",
        "NEXUS_DIAG_DISCOVERY_URL",
        "MANYFOLD_RUNTIME_ID",
        "MANYFOLD_SYNC_WEBHOOK_URL",
        "NARRA_SURFACE",
    ):
        monkeypatch.delenv(key, raising=False)
    # Isolate from any real opt-out marker on the developer machine.
    monkeypatch.setattr(_ship, "_OPTOUT_FILE", tmp_path / "telemetry_optout")


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
    return base  # url=None in overrides exercises the discovery path


class TestConsentGating:
    def test_default_is_full_with_lazy_url(self):
        # Telemetry consent model: ON by default (first-run disclosure
        # lives in the UI); the ingest URL resolves later via discovery.
        config = _ship.ship_config()
        assert config is not None
        assert config["mode"] == "full"
        assert config["url"] is None

    def test_env_off_silences(self, monkeypatch):
        monkeypatch.setenv("NEXUS_DIAG_SHIP", "off")
        assert _ship.ship_config() is None

    def test_optout_file_silences(self):
        _ship._OPTOUT_FILE.write_text("")
        assert _ship.ship_config() is None

    def test_env_override_beats_optout_file(self, monkeypatch):
        # Explicit env is the dev/self-host knob and outranks the UI
        # marker in both directions.
        _ship._OPTOUT_FILE.write_text("")
        monkeypatch.setenv("NEXUS_DIAG_SHIP", "meta")
        config = _ship.ship_config()
        assert config is not None and config["mode"] == "meta"

    def test_unknown_env_value_falls_to_default(self, monkeypatch):
        monkeypatch.setenv("NEXUS_DIAG_SHIP", "everything")
        config = _ship.ship_config()
        assert config is not None and config["mode"] == "full"

    def test_env_label_staging_from_manyfold_webhook(self, monkeypatch):
        monkeypatch.setenv(
            "MANYFOLD_SYNC_WEBHOOK_URL",
            "https://api-staging.manyfold.ai/api/internal/narranexus-sync/notify",
        )
        assert _ship.ship_config()["env"] == "staging"

    def test_env_label_explicit_wins(self, monkeypatch):
        monkeypatch.setenv("NEXUS_DIAG_ENV", "canary")
        monkeypatch.setenv(
            "MANYFOLD_SYNC_WEBHOOK_URL", "https://api-staging.manyfold.ai/x"
        )
        assert _ship.ship_config()["env"] == "canary"

    def test_meta_level_is_audit_and_up(self):
        assert _ship.ship_sink_level("meta") == 25
        assert _ship.ship_sink_level("meta", "DEBUG") == 25

    def test_full_level_follows_file_sink(self):
        # "full ships what the file sink sees" — DEBUG turned on for an
        # incident must reach the collector, not just the local file.
        assert _ship.ship_sink_level("full", "DEBUG") == "DEBUG"
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


class TestCircuitBreaker:
    def test_opens_after_threshold_and_drops_at_the_door(self, monkeypatch):
        """A dead collector must not turn the sink into a memory leak:
        after the threshold the breaker drops records without buffering
        or send attempts."""
        def _boom(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("dead", request=request)

        transport = httpx.MockTransport(_boom)
        calls = {"n": 0}

        class _CountingTransport(httpx.BaseTransport):
            def handle_request(self, request):
                calls["n"] += 1
                return transport.handle_request(request)

        monkeypatch.setattr(_ship, "_transport_for_tests", _CountingTransport())
        sink = _ship.ShipSink("backend", _config())
        for _ in range(_ship._BREAKER_THRESHOLD):
            sink(_message("x"))
            sink.flush()
        assert calls["n"] == _ship._BREAKER_THRESHOLD
        assert sink._cooldown_until > 0  # breaker open

        # While open: records dropped at the door, no buffering, no sends.
        sink(_message("dropped"))
        sink.flush()
        assert calls["n"] == _ship._BREAKER_THRESHOLD
        assert sink._buf == []
        assert sink._dropped_in_cooldown >= 1

    def test_half_open_after_cooldown_resumes(self, monkeypatch):
        rec = _Recorder()
        monkeypatch.setattr(_ship, "_transport_for_tests", rec.transport())
        sink = _ship.ShipSink("backend", _config())
        sink._cooldown_until = 1.0  # already elapsed on the monotonic clock
        sink._dropped_in_cooldown = 3
        sink(_message("after cooldown"))
        sink.flush()
        assert len(rec.requests) == 1
        assert sink._cooldown_until == 0.0


class TestBreakerHalfOpen:
    def test_probe_failure_reopens_immediately(self, monkeypatch):
        """Half-open means ONE failed probe re-opens — not re-earning the
        threshold (the doc-only version wasted 5 batches × 2s timeout per
        cooldown cycle against a long-dead collector)."""
        def _boom(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("dead", request=request)

        calls = {"n": 0}

        class _Counting(httpx.BaseTransport):
            def handle_request(self, request):
                calls["n"] += 1
                raise httpx.ConnectError("dead", request=request)

        monkeypatch.setattr(_ship, "_transport_for_tests", _Counting())
        sink = _ship.ShipSink("backend", _config())
        sink._cooldown_until = 1.0  # elapsed → next record enters half-open
        sink(_message("probe"))
        sink.flush()
        assert calls["n"] == 1
        assert sink._cooldown_until > 1.0  # re-opened by ONE failure
        assert sink._half_open is False

    def test_probe_success_closes_fully(self, monkeypatch):
        rec = _Recorder()
        monkeypatch.setattr(_ship, "_transport_for_tests", rec.transport())
        sink = _ship.ShipSink("backend", _config())
        sink._cooldown_until = 1.0
        sink(_message("probe"))
        sink.flush()
        assert len(rec.requests) == 1
        assert sink._cooldown_until == 0.0
        assert sink._half_open is False


class TestPermanentRejection:
    def test_4xx_drops_without_breaker_accounting(self, monkeypatch):
        """413 means 'this batch is unacceptable', not 'collector down' —
        it must never open the breaker."""
        rec = _Recorder(status=413)
        monkeypatch.setattr(_ship, "_transport_for_tests", rec.transport())
        sink = _ship.ShipSink("backend", _config())
        for _ in range(_ship._BREAKER_THRESHOLD * 2):
            sink(_message("big"))
            sink.flush()
        assert sink._fail_streak == 0
        assert sink._cooldown_until == 0.0  # breaker never opened
        assert len(rec.requests) == _ship._BREAKER_THRESHOLD * 2


class TestByteBatching:
    def test_flushes_on_serialized_bytes(self, monkeypatch):
        rec = _Recorder()
        monkeypatch.setattr(_ship, "_transport_for_tests", rec.transport())
        monkeypatch.setattr(_ship, "_BATCH_MAX_BYTES", 1000)
        sink = _ship.ShipSink("backend", _config())
        sink(_message("x" * 600))
        assert rec.requests == []  # under the byte cap, buffered
        sink(_message("y" * 600))
        assert len(rec.requests) == 1  # byte cap crossed → flushed early
        assert len(rec.batches()[0]) == 2


class TestAtexitSweep:
    def test_module_level_handler_flushes_live_sinks(self, monkeypatch):
        rec = _Recorder()
        monkeypatch.setattr(_ship, "_transport_for_tests", rec.transport())
        sink = _ship.ShipSink("backend", _config())
        sink(_message("tail line"))
        assert rec.requests == []
        _ship._flush_all_at_exit()
        assert len(rec.requests) == 1
        assert rec.batches()[0][0]["message"] == "tail line"


class TestDiscovery:
    def _routing_transport(self, mapping, hits):
        def _handle(request: httpx.Request) -> httpx.Response:
            if request.method == "GET":
                hits.append(str(request.url))
                return httpx.Response(200, json={"ingest": mapping})
            hits.append("POST")
            return httpx.Response(200, json={"ok": True})

        return httpx.MockTransport(_handle)

    def test_resolves_by_env_label_and_caches(self, monkeypatch):
        hits: list = []
        mapping = {
            "default": "https://prod.example.com/v1/ingest",
            "staging": "https://dev.example.com/v1/ingest",
        }
        monkeypatch.setattr(
            _ship, "_transport_for_tests", self._routing_transport(mapping, hits)
        )
        sink = _ship.ShipSink("backend", _config(url=None, env="staging"))
        sink(_message("a"))
        sink.flush()
        sink(_message("b"))
        sink.flush()
        # One discovery GET (TTL-cached), two ingest POSTs.
        gets = [h for h in hits if h != "POST"]
        assert len(gets) == 1
        assert hits.count("POST") == 2
        assert sink._resolved_url == "https://dev.example.com/v1/ingest"

    def test_unknown_label_falls_back_to_default(self, monkeypatch):
        hits: list = []
        mapping = {"default": "https://prod.example.com/v1/ingest"}
        monkeypatch.setattr(
            _ship, "_transport_for_tests", self._routing_transport(mapping, hits)
        )
        sink = _ship.ShipSink("backend", _config(url=None, env="local"))
        sink(_message("x"))
        sink.flush()
        assert sink._resolved_url == "https://prod.example.com/v1/ingest"

    def test_unresolvable_discovery_drops_quietly(self, monkeypatch):
        def _dead(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("no dns", request=request)

        monkeypatch.setattr(
            _ship, "_transport_for_tests", httpx.MockTransport(_dead)
        )
        sink = _ship.ShipSink("backend", _config(url=None))
        sink(_message("x"))
        sink.flush()  # must not raise; batch dropped, breaker untouched
        assert sink._fail_streak == 0
        assert sink._cooldown_until == 0.0

    def test_env_url_override_skips_discovery(self, monkeypatch):
        rec = _Recorder()
        monkeypatch.setattr(_ship, "_transport_for_tests", rec.transport())
        sink = _ship.ShipSink("backend", _config())  # url set in _config
        sink(_message("x"))
        sink.flush()
        assert len(rec.requests) == 1  # straight POST, no GET
        assert rec.requests[0].method == "POST"
