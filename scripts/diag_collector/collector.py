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
    DIAG_COLLECT_MAX_DATA_GB      default 20 — HARD footprint cap: the
                                  collector deletes its own oldest
                                  files to stay under it. The host's
                                  disk can never fill because of
                                  telemetry; the worst an attacker
                                  achieves is rotating OUR buffer.
    DIAG_COLLECT_PORT             default 9880

Deployment constraint: expose ONLY behind our caddy, which must
overwrite X-Real-IP (identity for rate limiting) and should cap
request_body size at the same 8MB wire limit.

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


def _max_data_bytes() -> int:
    return int(
        float(os.environ.get("DIAG_COLLECT_MAX_DATA_GB", "20")) * 1024**3
    )


# Size-cap bookkeeping: a full tree scan per request would be absurd, so
# writes accumulate into a counter and the enforcement pass runs when
# the estimate says it could matter (or on the daily retention tick).
_size_check_appended = 0
_SIZE_CHECK_EVERY_BYTES = 512 * 1024 * 1024


def enforce_size_cap() -> int:
    """Delete oldest .jsonl files until the data dir fits the cap.

    THE load-bearing defense of the public-endpoint design: the
    collector's footprint is capped by construction, independent of any
    judgment about the sender. A flood degrades to "our telemetry
    buffer rotates faster", never "the shared host's disk fills".
    Returns files deleted."""
    root = _data_dir()
    if not root.is_dir():
        return 0
    files = []
    total = 0
    for path in root.rglob("*.jsonl"):
        try:
            stat = path.stat()
        except OSError:
            continue
        files.append((stat.st_mtime, stat.st_size, path))
        total += stat.st_size
    cap = _max_data_bytes()
    if total <= cap:
        return 0
    target = int(cap * 0.9)  # free headroom so we don't re-enter per write
    deleted = 0
    for _, size, path in sorted(files):
        if total <= target:
            break
        try:
            path.unlink()
            total -= size
            deleted += 1
        except OSError:
            continue
    if deleted:
        logger.warning(
            f"[diag-collector] size cap: dropped {deleted} oldest files "
            f"to stay under {cap} bytes"
        )
    return deleted


# --- per-IP rate limiting (the public endpoint's actual defense) -----------

_RATE_WINDOW_S = 60.0
_RATE_MAX_REQUESTS = 120
_RATE_MAX_BYTES = 64 * 1024 * 1024  # wire bytes per IP per window
_RATE_MAX_IPS = 10_000
# Global (all-IPs) budget: the layer that still binds when an attacker
# rotates identities. Per-IP fairness is best-effort; this is the cap.
_GLOBAL_MAX_BYTES = 256 * 1024 * 1024

_rate_state: dict[str, list[tuple[float, int]]] = {}
_global_window: list[tuple[float, int]] = []


def _client_ip(request: Request) -> str:
    """Trusted-hop identity ONLY: X-Real-IP as overwritten by OUR
    caddy (deployment constraint: the collector is reachable solely
    through it). X-Forwarded-For's first element is client-authored —
    keying buckets on it hands every attacker a fresh bucket per
    request."""
    real_ip = request.headers.get("x-real-ip", "").strip()
    if real_ip:
        return real_ip
    return request.client.host if request.client else "unknown"


def _prune(window: list[tuple[float, int]], now: float) -> list[tuple[float, int]]:
    return [(ts, b) for ts, b in window if now - ts < _RATE_WINDOW_S]


def _rate_precheck(request: Request) -> str:
    """Request-count gate BEFORE reading the body. Byte budgets settle
    after the stream is read (Content-Length is client-authored)."""
    now = time.monotonic()
    ip = _client_ip(request)
    if len(_rate_state) > _RATE_MAX_IPS:
        # Evict the ~10% least-recently-active IPs. clear() was an
        # attacker primitive: flood with fake identities, reset
        # EVERYONE's counters including your own.
        stale = sorted(
            _rate_state.items(),
            key=lambda kv: kv[1][-1][0] if kv[1] else 0.0,
        )[: max(1, _RATE_MAX_IPS // 10)]
        for key, _ in stale:
            _rate_state.pop(key, None)
    window = _prune(_rate_state.get(ip, []), now)
    if len(window) >= _RATE_MAX_REQUESTS:
        _rate_state[ip] = window
        raise HTTPException(status_code=429, detail="rate limited")
    window.append((now, 0))
    _rate_state[ip] = window
    return ip


def _rate_settle_bytes(ip: str, wire_bytes: int) -> None:
    """Charge ACTUAL streamed bytes; reject when either budget is
    blown. Post-consumption by necessity — but storage (the resource
    that matters, see the size cap) is only spent on accepted requests."""
    now = time.monotonic()
    global _global_window
    window = _prune(_rate_state.get(ip, []), now)
    if window:
        ts, b = window[-1]
        window[-1] = (ts, b + wire_bytes)
    _rate_state[ip] = window
    _global_window = _prune(_global_window, now)
    _global_window.append((now, wire_bytes))
    if sum(b for _, b in window) > _RATE_MAX_BYTES:
        raise HTTPException(status_code=429, detail="rate limited")
    if sum(b for _, b in _global_window) > _GLOBAL_MAX_BYTES:
        raise HTTPException(status_code=429, detail="global budget exceeded")


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
    ip = _rate_precheck(request)
    chunks: list[bytes] = []
    total = 0
    async for chunk in request.stream():
        total += len(chunk)
        if total > _MAX_WIRE_BYTES:
            raise HTTPException(status_code=413, detail="request too large")
        chunks.append(chunk)
    _rate_settle_bytes(ip, total)
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

    global _size_check_appended
    _size_check_appended += total
    if _size_check_appended >= _SIZE_CHECK_EVERY_BYTES:
        _size_check_appended = 0
        await asyncio.to_thread(enforce_size_cap)

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
        await asyncio.to_thread(enforce_size_cap)
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
