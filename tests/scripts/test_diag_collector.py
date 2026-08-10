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
from datetime import date
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
        / f"{date.today():%Y-%m-%d}.jsonl"
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
