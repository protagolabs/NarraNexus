"""
@file_name: test_diag_collector.py
@author:
@date: 2026-08-10
@description: Diagnostic log collector — auth, gzip ingest, partitioned
JSONL storage, path-segment sanitization, retention sweep.
"""
from __future__ import annotations

import gzip
import importlib.util
import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path

import httpx
import pytest
from httpx import ASGITransport

_SPEC = importlib.util.spec_from_file_location(
    "diag_collector",
    Path(__file__).resolve().parents[2] / "scripts/diag_collector/collector.py",
)
collector = importlib.util.module_from_spec(_SPEC)
assert _SPEC and _SPEC.loader
_SPEC.loader.exec_module(collector)


@pytest.fixture
def env(monkeypatch, tmp_path):
    monkeypatch.setenv("DIAG_COLLECT_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("DIAG_COLLECT_TOKEN", "coll-tok")
    monkeypatch.setattr(collector, "_rate_counts", {})
    monkeypatch.setattr(collector, "_rate_bytes", {})
    return tmp_path


def _lines(*messages, env_label="manyfold-staging", runtime="rt_x", service="backend"):
    return "\n".join(
        json.dumps(
            {
                "ts": "2026-08-10T12:00:00+00:00",
                "level": "INFO",
                "service": service,
                "env": env_label,
                "runtime_id": runtime,
                "message": m,
            }
        )
        for m in messages
    )


async def _post(body: bytes, *, token="coll-tok", gzipped=True):
    headers = {}
    if token is not None:
        headers["Authorization"] = f"Bearer {token}"
    if gzipped:
        headers["Content-Encoding"] = "gzip"
    transport = ASGITransport(app=collector.app)
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as c:
        return await c.post("/v1/ingest", content=body, headers=headers)


async def test_ingest_writes_partitioned_jsonl(env):
    resp = await _post(gzip.compress(_lines("a", "b").encode()))
    assert resp.status_code == 200
    assert resp.json()["accepted"] == 2
    target = (
        env
        / "manyfold-staging"
        / "rt_x"
        / "backend"
        / f"{datetime.now(timezone.utc).date():%Y-%m-%d}.jsonl"
    )
    assert target.is_file()
    rows = [json.loads(ln) for ln in target.read_text().splitlines()]
    assert [r["message"] for r in rows] == ["a", "b"]
    assert all("received_at" in r for r in rows)


async def test_ingest_appends_across_batches(env):
    await _post(gzip.compress(_lines("one").encode()))
    await _post(gzip.compress(_lines("two").encode()))
    target = next(env.rglob("*.jsonl"))
    assert len(target.read_text().splitlines()) == 2


async def test_bad_token_rejected(env):
    resp = await _post(gzip.compress(_lines("x").encode()), token="wrong")
    assert resp.status_code == 401


async def test_plain_body_accepted(env):
    resp = await _post(_lines("plain").encode(), gzipped=False)
    assert resp.status_code == 200


async def test_bad_gzip_rejected(env):
    resp = await _post(b"not-gzip-at-all")
    assert resp.status_code == 400


async def test_broken_line_skipped_not_fatal(env):
    body = _lines("good") + "\n{broken json\n" + _lines("also good")
    resp = await _post(gzip.compress(body.encode()))
    assert resp.json()["accepted"] == 2


async def test_path_segments_sanitized(env):
    body = _lines("evil", env_label="../../etc", runtime="a/b", service="")
    resp = await _post(gzip.compress(body.encode()))
    assert resp.status_code == 200
    written = list(env.rglob("*.jsonl"))
    assert len(written) == 1
    rel = written[0].relative_to(env)
    # No separator survives sanitization; empty segments become "unknown".
    assert ".." not in rel.parts
    assert rel.parts[0] == "etc" or "_" in rel.parts[0]
    assert "unknown" in rel.parts


async def test_retention_sweep_deletes_old_files(env):
    await _post(gzip.compress(_lines("keep").encode()))
    old = env / "e" / "r" / "s" / "2020-01-01.jsonl"
    old.parent.mkdir(parents=True)
    old.write_text("{}\n")
    stale = time.time() - 90 * 86400
    os.utime(old, (stale, stale))
    deleted = collector.sweep_retention()
    assert deleted == 1
    assert not old.exists()
    assert list(env.rglob("*.jsonl"))  # fresh file survived


async def test_gzip_bomb_bounded_413(env, monkeypatch):
    """The cap fires WHILE inflating — a small request must never fully
    materialize past the limit."""
    monkeypatch.setattr(collector, "_MAX_BODY_BYTES", 1024)
    bomb = gzip.compress(b"0" * 100_000)  # tiny wire, big inflate
    resp = await _post(bomb)
    assert resp.status_code == 413


async def test_truncated_gzip_is_400_not_500(env):
    truncated = gzip.compress(_lines("x").encode())[:-5]  # EOFError path
    resp = await _post(truncated)
    assert resp.status_code == 400


async def test_no_token_is_public_mode(monkeypatch, tmp_path):
    """Telemetry redesign (2026-08-11): the collector is a PUBLIC
    endpoint by default — open-source senders can't hold a secret, so
    defense is rate limiting + size caps, not tokens."""
    monkeypatch.setenv("DIAG_COLLECT_DATA_DIR", str(tmp_path))
    monkeypatch.delenv("DIAG_COLLECT_TOKEN", raising=False)
    resp = await _post(gzip.compress(_lines("x").encode()), token=None)
    assert resp.status_code == 200


async def test_set_token_is_still_enforced(env):
    # Private-deployment knob: once configured, it is required.
    resp = await _post(gzip.compress(_lines("x").encode()), token="wrong")
    assert resp.status_code == 401


async def test_rate_limit_429(env, monkeypatch):
    monkeypatch.setattr(collector, "_RATE_MAX_REQUESTS", 2)
    monkeypatch.setattr(collector, "_rate_counts", {})
    monkeypatch.setattr(collector, "_rate_bytes", {})
    body = gzip.compress(_lines("x").encode())
    assert (await _post(body)).status_code == 200
    assert (await _post(body)).status_code == 200
    assert (await _post(body)).status_code == 429


async def test_discovery_config_served_verbatim(env, monkeypatch):
    doc = {"ingest": {"default": "https://a/v1/ingest", "staging": "https://b/v1/ingest"}}
    monkeypatch.setenv("DIAG_COLLECT_CONFIG_JSON", json.dumps(doc))
    transport = ASGITransport(app=collector.app)
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as c:
        resp = await c.get("/v1/config")
    assert resp.status_code == 200
    assert resp.json() == doc


async def test_discovery_config_404_when_unset(env, monkeypatch):
    monkeypatch.delenv("DIAG_COLLECT_CONFIG_JSON", raising=False)
    transport = ASGITransport(app=collector.app)
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as c:
        resp = await c.get("/v1/config")
    assert resp.status_code == 404


async def test_wire_size_cap_precedes_buffering(env, monkeypatch):
    monkeypatch.setattr(collector, "_MAX_WIRE_BYTES", 1024)
    resp = await _post(b"0" * 10_000, gzipped=False)
    assert resp.status_code == 413


async def test_non_ascii_auth_header_is_401_not_500(env):
    transport = ASGITransport(app=collector.app)
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as c:
        resp = await c.post(
            "/v1/ingest",
            content=b"{}",
            # bytes value: httpx's own header validation would refuse the
            # non-ASCII str before it ever reached the server
            headers={"Authorization": "Bearer café-token".encode("latin-1")},
        )
    assert resp.status_code == 401


async def test_rotating_identity_headers_cannot_mint_buckets(env, monkeypatch):
    """Without DIAG_COLLECT_TRUST_REAL_IP, BOTH identity headers are
    ignored (X-Real-IP is client-sendable too) — rotating them must not
    mint fresh buckets; everyone shares the peer-address bucket."""
    monkeypatch.delenv("DIAG_COLLECT_TRUST_REAL_IP", raising=False)
    monkeypatch.setattr(collector, "_RATE_MAX_REQUESTS", 2)
    body = gzip.compress(_lines("x").encode())
    transport = ASGITransport(app=collector.app)
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as c:
        for i in range(3):
            resp = await c.post(
                "/v1/ingest",
                content=body,
                headers={
                    "Authorization": "Bearer coll-tok",
                    "Content-Encoding": "gzip",
                    "X-Forwarded-For": f"10.0.0.{i}",  # attacker-rotated
                    "X-Real-IP": f"10.1.0.{i}",  # attacker-rotated too
                },
            )
    assert resp.status_code == 429


async def test_trusted_hop_used_only_when_flagged(env, monkeypatch):
    """With the flag on (deployment declares caddy overwrites the
    header), X-Real-IP keys the buckets — distinct real clients get
    distinct budgets."""
    monkeypatch.setenv("DIAG_COLLECT_TRUST_REAL_IP", "1")
    monkeypatch.setattr(collector, "_RATE_MAX_REQUESTS", 1)
    body = gzip.compress(_lines("x").encode())
    transport = ASGITransport(app=collector.app)
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as c:
        r1 = await c.post(
            "/v1/ingest", content=body,
            headers={"Authorization": "Bearer coll-tok",
                     "Content-Encoding": "gzip", "X-Real-IP": "203.0.113.7"},
        )
        r2 = await c.post(
            "/v1/ingest", content=body,
            headers={"Authorization": "Bearer coll-tok",
                     "Content-Encoding": "gzip", "X-Real-IP": "203.0.113.8"},
        )
        r3 = await c.post(
            "/v1/ingest", content=body,
            headers={"Authorization": "Bearer coll-tok",
                     "Content-Encoding": "gzip", "X-Real-IP": "203.0.113.7"},
        )
    assert (r1.status_code, r2.status_code, r3.status_code) == (200, 200, 429)


async def test_byte_budget_uses_actual_stream_not_header(env, monkeypatch):
    monkeypatch.setattr(collector, "_RATE_MAX_BYTES", 100)
    monkeypatch.setattr(collector, "_rate_counts", {})
    monkeypatch.setattr(collector, "_rate_bytes", {})
    big = _lines("y" * 400).encode()  # plain, well over 100 bytes actual
    transport = ASGITransport(app=collector.app)
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as c:
        resp = await c.post(
            "/v1/ingest",
            content=big,
            headers={
                "Authorization": "Bearer coll-tok",
                # lying header must not matter — settlement reads the stream
                "Content-Length": str(len(big)),
            },
        )
    assert resp.status_code == 429


async def test_size_cap_deletes_oldest_first(env, monkeypatch):
    d = env / "e" / "r" / "s"
    d.mkdir(parents=True)
    old = d / "2026-01-01.jsonl"
    new = d / "2026-06-01.jsonl"
    old.write_text("o" * 600)
    new.write_text("n" * 600)
    os.utime(old, (1000, 1000))
    monkeypatch.setenv("DIAG_COLLECT_MAX_DATA_GB", str(1000 / 1024**3))  # 1000 bytes
    deleted = collector.enforce_size_cap()
    assert deleted == 1
    assert not old.exists() and new.exists()


async def test_size_cap_noop_under_limit(env):
    (env / "a.jsonl").write_text("x")
    assert collector.enforce_size_cap() == 0


async def test_size_cap_drains_largest_partition_first(env, monkeypatch):
    """A flood squeezes ITSELF out: deletion drains the biggest
    env/runtime partition's oldest files before touching known
    senders' data."""
    flood = env / "unknown" / "rt_flood" / "s"
    ours = env / "manyfold-prod" / "rt_ours" / "s"
    flood.mkdir(parents=True)
    ours.mkdir(parents=True)
    for i in range(3):
        f = flood / f"2026-0{i+1}-01.jsonl"
        f.write_text("f" * 300)
        os.utime(f, (1000 + i, 1000 + i))
    keep = ours / "2026-01-01.jsonl"
    keep.write_text("o" * 100)
    os.utime(keep, (500, 500))  # OLDER than everything in the flood
    monkeypatch.setenv("DIAG_COLLECT_MAX_DATA_GB", str(600 / 1024**3))
    deleted = collector.enforce_size_cap()
    # Global-oldest deletion would have taken `keep` first; partition-
    # aware deletion drains the flood instead.
    assert keep.exists()
    assert deleted >= 2
