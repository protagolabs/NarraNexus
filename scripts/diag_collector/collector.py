"""
@file_name: collector.py
@author:
@date: 2026-08-10
@description: Diagnostic log collector — the receiving end of the
observability push sink (utils/logging/_ship.py).

Runs on OUR ops hosts (staging sandboxes → NarraNexus-dev, prod →
xyz-algo), NOT inside any sandbox. TLS is terminated by the host's
existing caddy; this service listens on a local port behind it. Auth is
a static bearer token shared with the senders via env — service-to-
service HTTP, no SSH/pem involved (pem stays what HUMANS use to log in
and read the collected files).

Storage layout (grep-first: every record is one self-contained JSON
line with the envelope fields merged in):

    <DATA_DIR>/<env>/<runtime_id>/<service>/<YYYY-MM-DD>.jsonl

Retention: files older than DIAG_COLLECT_RETENTION_DAYS (default 30)
are deleted by a daily sweep (startup + every 24h).

Env:
    DIAG_COLLECT_TOKEN            bearer the senders must present
                                  (empty = accept unauthenticated —
                                  only sane behind a private network)
    DIAG_COLLECT_DATA_DIR         default ~/diag-collect
    DIAG_COLLECT_RETENTION_DAYS   default 30
    DIAG_COLLECT_PORT             default 9880

Run: uv run python scripts/diag_collector/collector.py
"""
from __future__ import annotations

import asyncio
import gzip
import json
import os
import re
import time
from datetime import date, datetime, timezone
from pathlib import Path

from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request
from loguru import logger


@asynccontextmanager
async def _lifespan(app: FastAPI):
    task = asyncio.get_running_loop().create_task(_retention_loop())
    yield
    task.cancel()


app = FastAPI(title="narranexus-diag-collector", lifespan=_lifespan)

_SAFE_SEGMENT_RE = re.compile(r"[^a-zA-Z0-9_\-.]")
_MAX_BODY_BYTES = 32 * 1024 * 1024  # decompressed


def _data_dir() -> Path:
    return Path(
        os.environ.get("DIAG_COLLECT_DATA_DIR", "")
        or Path.home() / "diag-collect"
    )


def _retention_days() -> int:
    return int(os.environ.get("DIAG_COLLECT_RETENTION_DAYS", "30"))


def _require_auth(request: Request) -> None:
    token = os.environ.get("DIAG_COLLECT_TOKEN", "").strip()
    if not token:
        return
    header = request.headers.get("authorization", "")
    if header != f"Bearer {token}":
        raise HTTPException(status_code=401, detail="bad token")


def _segment(value: str) -> str:
    """One path segment from untrusted sender metadata: whitelist chars,
    never empty, never a dot-name (path traversal is structurally
    impossible — no separators survive)."""
    cleaned = _SAFE_SEGMENT_RE.sub("_", str(value or ""))[:64].strip("._")
    return cleaned or "unknown"


@app.post("/v1/ingest")
async def ingest(request: Request):
    _require_auth(request)
    raw = await request.body()
    if request.headers.get("content-encoding", "").lower() == "gzip":
        try:
            raw = gzip.decompress(raw)
        except OSError:
            raise HTTPException(status_code=400, detail="bad gzip body")
    if len(raw) > _MAX_BODY_BYTES:
        raise HTTPException(status_code=413, detail="batch too large")

    accepted = 0
    files: dict[Path, list[str]] = {}
    for line in raw.decode("utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            record = json.loads(line)
        except ValueError:
            continue  # one broken line must not sink the batch
        target = (
            _data_dir()
            / _segment(record.get("env", ""))
            / _segment(record.get("runtime_id", ""))
            / _segment(record.get("service", ""))
            / f"{date.today():%Y-%m-%d}.jsonl"
        )
        record["received_at"] = datetime.now(timezone.utc).isoformat()
        files.setdefault(target, []).append(
            json.dumps(record, ensure_ascii=False)
        )
        accepted += 1

    for target, lines in files.items():
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open("a", encoding="utf-8") as fh:
            fh.write("\n".join(lines) + "\n")

    return {"ok": True, "accepted": accepted}


@app.get("/healthz")
async def healthz():
    return {"ok": True, "data_dir": str(_data_dir())}


def sweep_retention() -> int:
    """Delete .jsonl files older than the retention window. Returns count."""
    cutoff = time.time() - _retention_days() * 86400
    deleted = 0
    root = _data_dir()
    if not root.is_dir():
        return 0
    for path in root.rglob("*.jsonl"):
        try:
            if path.stat().st_mtime < cutoff:
                path.unlink()
                deleted += 1
        except OSError:
            continue
    return deleted


async def _retention_loop() -> None:
    while True:
        deleted = await asyncio.to_thread(sweep_retention)
        if deleted:
            logger.info(f"[diag-collector] retention: dropped {deleted} files")
        await asyncio.sleep(24 * 3600)


def main() -> None:
    import uvicorn

    port = int(os.environ.get("DIAG_COLLECT_PORT", "9880"))
    uvicorn.run(app, host="0.0.0.0", port=port)


if __name__ == "__main__":
    main()
