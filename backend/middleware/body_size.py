"""
@file_name: body_size.py
@author: NetMind.AI
@date: 2026-08-20
@description: The ONE layer where a declared-length body cap can actually
run before any byte is buffered.

FastAPI reads and parses the request body BEFORE it resolves route
dependencies (fastapi/routing.py: `await request.body()` /
`await request.form()` happen ahead of `solve_dependencies`), so a
`Depends(...)` size gate on a route with a body field executes only after
the framework has already parked the whole payload in memory — a
structurally fake door (review #334 r3 I1; two of the three r1-I3 gates
were exactly this). HTTP middleware wraps the app, so this check fires
before the framework touches the stream.

Per-route caps, NOT one global number: the write entrances span three
orders of magnitude (64KB edit commands vs 25MB document content) and a
single limit is necessarily wrong on one side. Content-Length can lie or
be absent (chunked) — the in-handler streamed accumulation caps stay as
the second gate wherever the handler owns the stream; for UploadFile
routes the framework spools to disk and THIS declared-length check plus
the disk are the honest bound.
"""

from __future__ import annotations

import re
from typing import List, Tuple

from fastapi import Request
from fastapi.responses import JSONResponse

_MB = 1024 * 1024

#: (methods, path regex, max declared bytes). First match wins.
BODY_CAPS: List[Tuple[frozenset, re.Pattern, int]] = [
    (
        frozenset({"PUT"}),
        re.compile(r"^/api/agents/[^/]+/artifacts/[^/]+/content$"),
        27 * _MB,  # MAX_ARTIFACT_BYTES + JSON quoting margin
    ),
    (
        frozenset({"POST"}),
        re.compile(r"^/api/agents/[^/]+/artifacts/[^/]+/office-asset$"),
        11 * _MB,  # 10 MB asset + multipart framing margin
    ),
    (
        frozenset({"POST"}),
        re.compile(r"^/api/office-watch/edit$"),
        64 * 1024,
    ),
    (
        frozenset({"POST"}),
        re.compile(r"^/api/public/office-watch-proxy/"),
        64 * 1024,
    ),
]


async def body_size_middleware(request: Request, call_next):
    declared = request.headers.get("content-length")
    if declared and declared.isdigit():
        size = int(declared)
        for methods, pattern, cap in BODY_CAPS:
            if request.method in methods and pattern.match(request.url.path):
                if size > cap:
                    return JSONResponse(
                        status_code=413, content={"detail": "request body too large"}
                    )
                break
    return await call_next(request)
