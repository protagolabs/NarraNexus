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

This is a PUBLIC telemetry endpoint by design (telemetry redesign,
2026-08-11): the sender codebase is open source, so a baked or shared
client secret authenticates nothing — security here is abuse control
(streaming size caps, gzip-bomb bounds, per-IP rate limits), not
secrecy. A spoofed envelope pollutes only our own diagnostics data.
``DIAG_COLLECT_TOKEN`` remains an optional knob for private
deployments; when set, requests must present it.

Env:
    DIAG_COLLECT_TOKEN            optional bearer; set → required on
                                  every request (private deployments)
    DIAG_COLLECT_CONFIG_JSON      optional JSON served verbatim at
                                  GET /telemetry/v1/config — the
                                  discovery document senders resolve
                                  ingest URLs from (set on the PROD
                                  collector; carries the staging URL
                                  too)
    DIAG_COLLECT_DATA_DIR         default ~/diag-collect
    DIAG_COLLECT_RETENTION_DAYS   default 30
    DIAG_COLLECT_PORT             default 9880

Run: uv run python scripts/diag_collector/collector.py
"""
from __future__ import annotations

import asyncio
import gzip
import hmac
import io
import json
import os
import re
import time
import zlib
from datetime import datetime, timezone
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
# Wire-size cap enforced WHILE reading the request — request.body()
# would buffer an arbitrarily large plaintext POST before any check
# ran (the gzip-bomb guard bounds amplification, not raw size).
_MAX_WIRE_BYTES = 8 * 1024 * 1024


def _bounded_decompress(raw: bytes) -> bytes:
    """Streaming gunzip with the size cap enforced WHILE inflating — a
    checked-after-the-fact limit is no limit at all against a gzip bomb
    (a few KB of request would fully materialize before the 413)."""
    out = io.BytesIO()
    try:
        with gzip.GzipFile(fileobj=io.BytesIO(raw)) as gz:
            while True:
                chunk = gz.read(1024 * 1024)
                if not chunk:
                    break
                out.write(chunk)
                if out.tell() > _MAX_BODY_BYTES:
                    raise HTTPException(status_code=413, detail="batch too large")
    # gzip.decompress raises EOFError on truncation and zlib.error on
    # corruption — neither is an OSError; catching OSError alone turns a
    # malformed body into a 500.
    except (OSError, EOFError, zlib.error):
        raise HTTPException(status_code=400, detail="bad gzip body")
    return out.getvalue()


def _data_dir() -> Path:
    return Path(
        os.environ.get("DIAG_COLLECT_DATA_DIR", "")
        or Path.home() / "diag-collect"
    )


def _retention_days() -> int:
    return int(os.environ.get("DIAG_COLLECT_RETENTION_DAYS", "30"))


# --- per-IP rate limiting (the public endpoint's actual defense) -----------

_RATE_WINDOW_S = 60.0
_RATE_MAX_REQUESTS = 120
_RATE_MAX_BYTES = 64 * 1024 * 1024  # wire bytes per IP per window
_RATE_MAX_IPS = 10_000

_rate_state: dict[str, list[tuple[float, int]]] = {}


def _client_ip(request: Request) -> str:
    forwarded = request.headers.get("x-forwarded-for", "")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


def _rate_limit(request: Request, wire_bytes: int) -> None:
    """Sliding-window per-IP limit on request count and wire bytes.
    In-memory on purpose: a restart forgiving all counters is fine for
    abuse control, and the collector is a single process."""
    now = time.monotonic()
    ip = _client_ip(request)
    if len(_rate_state) > _RATE_MAX_IPS:
        _rate_state.clear()  # crude flood shed; counters are advisory
    window = [
        (ts, b) for ts, b in _rate_state.get(ip, [])
        if now - ts < _RATE_WINDOW_S
    ]
    if (
        len(window) >= _RATE_MAX_REQUESTS
        or sum(b for _, b in window) + wire_bytes > _RATE_MAX_BYTES
    ):
        _rate_state[ip] = window
        raise HTTPException(status_code=429, detail="rate limited")
    window.append((now, wire_bytes))
    _rate_state[ip] = window


def _require_auth(request: Request) -> None:
    token = os.environ.get("DIAG_COLLECT_TOKEN", "").strip()
    if not token:
        # Public mode — the default for the open-source telemetry
        # design. Defense is the rate limiter + size caps, not secrecy.
        return
    header = request.headers.get("authorization", "")
    # Constant-time comparison, over BYTES: compare_digest raises
    # TypeError on non-ASCII str (headers decode as latin-1, so any
    # >127 byte in the header would turn a bad token into a 500).
    if not hmac.compare_digest(
        header.encode("utf-8", errors="replace"),
        f"Bearer {token}".encode(),
    ):
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
    _rate_limit(request, int(request.headers.get("content-length", "0") or 0))
    chunks: list[bytes] = []
    total = 0
    async for chunk in request.stream():
        total += len(chunk)
        if total > _MAX_WIRE_BYTES:
            raise HTTPException(status_code=413, detail="request too large")
        chunks.append(chunk)
    raw = b"".join(chunks)
    if request.headers.get("content-encoding", "").lower() == "gzip":
        # (plaintext needs no second cap: the wire cap above already
        # bounds it below _MAX_BODY_BYTES — a dead branch here would be
        # a kept-just-in-case path, rule #2)
        raw = _bounded_decompress(raw)

    accepted = 0
    files: dict[Path, list[str]] = {}
    # Partition by UTC date — records carry UTC ts, and a local-date
    # filename would disagree with its own contents around midnight.
    day = datetime.now(timezone.utc).date()
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
            / f"{day:%Y-%m-%d}.jsonl"
        )
        record["received_at"] = datetime.now(timezone.utc).isoformat()
        files.setdefault(target, []).append(
            json.dumps(record, ensure_ascii=False)
        )
        accepted += 1

    def _write_all() -> None:
        for target, lines in files.items():
            target.parent.mkdir(parents=True, exist_ok=True)
            with target.open("a", encoding="utf-8") as fh:
                fh.write("\n".join(lines) + "\n")

    # Disk writes off the event loop (same stance as _retention_loop) —
    # a large batch inline would stall every other sender's POST past
    # their 2s send timeout, which on the sender side means dropped
    # batches.
    await asyncio.to_thread(_write_all)

    return {"ok": True, "accepted": accepted}


@app.get("/healthz")
async def healthz():
    return {"ok": True, "data_dir": str(_data_dir())}


@app.get("/v1/config")
async def discovery_config():
    """The discovery document senders resolve their ingest URL from
    (see _ship.py). Served verbatim from env so URL rotation is an env
    change on ONE host — the prod collector — with zero client releases.
    Public and unauthenticated on purpose: it contains only URLs."""
    raw = os.environ.get("DIAG_COLLECT_CONFIG_JSON", "").strip()
    if not raw:
        raise HTTPException(status_code=404, detail="no discovery config set")
    try:
        return json.loads(raw)
    except ValueError:
        raise HTTPException(
            status_code=500, detail="DIAG_COLLECT_CONFIG_JSON is not valid JSON"
        ) from None


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

    if not os.environ.get("DIAG_COLLECT_TOKEN", "").strip():
        logger.info(
            "[diag-collector] running PUBLIC (no token) — the intended "
            "open-source telemetry mode; abuse control = rate limits + caps"
        )
    port = int(os.environ.get("DIAG_COLLECT_PORT", "9880"))
    uvicorn.run(app, host="0.0.0.0", port=port)


if __name__ == "__main__":
    main()
