"""
@file_name: _confined_response.py
@author: Bin Liang
@date: 2026-10-30
@description: Shared route helpers for serving tenant files without a
check-then-open-by-path window: ``open_tenant_file`` and a Starlette
``FileResponse`` that serves an ALREADY-OPEN file object.

Routes used to hand ``FileResponse`` the resolved path string, and Starlette
opens it again when the first byte is sent. A tenant who swaps a directory on
that path for a symlink between resolution and sending made the backend serve
another tenant's file. Routes now open the file through ``utils/confined_io``
(every component below the workspace base O_NOFOLLOW) and give the response
class the resulting file object; headers (Content-Length / Last-Modified / ETag /
Accept-Ranges), HEAD, single and multi Range, If-Range and 416 behave exactly
as Starlette's because the framing logic is inherited — only the three body
writers that touch the file are replaced to read from the held object.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import BinaryIO, Iterable, Mapping, Optional, Union

import anyio
from loguru import logger
from starlette.responses import FileResponse
from starlette.types import Receive, Scope, Send

from narranexus.platform.settings import settings
from narranexus.platform.utils import confined_io


def open_tenant_file(
    path: Union[str, Path], *, within: Iterable[Union[str, Path]] = ()
) -> Optional[BinaryIO]:
    """Open a resolved tenant-tree file for reading through the link-free walk.

    ``path`` is what a resolver (``resolve_attachment_path``,
    ``resolve_shared_file_for_user``, workspace ``_resolve_within_workspace``)
    already proved to be inside the caller's tree; it is only a LABEL here: the
    base-relative tail is re-derived lexically and every component below
    ``base_working_path`` is opened O_NOFOLLOW, so a directory swapped for a
    link after the check is refused.

    ``within`` is the LEXICAL root(s) the file must live under (the caller's
    workspace / per-user root, built from ids — never resolved). It closes the
    gap the walk alone cannot: a resolver that returns a REALPATH hands back a
    link-free path even when a pre-planted link already redirected it into
    another tenant's tree, and only a prefix check against the caller's own
    root notices. Empty ``within`` skips the check (callers whose resolver
    already pins the root).

    Returns None when the file is missing, not a regular file, outside the base
    or ``within``, or the path crosses a link — callers map that to their own
    404 shape.
    """
    try:
        root, rel = confined_io.split_under_base(str(path), settings.base_working_path)
        roots = [
            confined_io.split_under_base(str(w), settings.base_working_path)[1] for w in within
        ]
        if roots and not any(rel.startswith(r + "/") for r in roots):
            logger.warning(f"tenant file refused (outside the caller's root): {path!s}")
            return None
        return confined_io.open_read(root, rel)
    except confined_io.ConfinedPathError as e:
        logger.warning(f"tenant file refused (link or outside base): {path!s}: {e}")
        return None
    except OSError:
        return None


class ConfinedFileResponse(FileResponse):
    """Serve ``file`` (open, binary, regular) with FileResponse semantics.

    The response owns ``file`` and closes it when the ASGI call ends — on
    success, on error, and for HEAD (which never reads it).
    """

    def __init__(
        self,
        file: BinaryIO,
        *,
        media_type: Optional[str] = None,
        headers: Optional[Mapping[str, str]] = None,
        status_code: int = 200,
        filename: Optional[str] = None,
    ) -> None:
        self._file = file
        try:
            stat_result = os.fstat(file.fileno())
        except BaseException:
            file.close()
            raise
        # ``path`` is only a label here: with stat_result given, Starlette never
        # stats or opens it, and the body writers below ignore it.
        super().__init__(
            path=f"<confined-fd:{file.fileno()}>",
            status_code=status_code,
            headers=headers,
            media_type=media_type,
            stat_result=stat_result,
            filename=filename,
        )

    def __del__(self) -> None:
        # A response that was built but never awaited (handler raised after
        # constructing it, client gone before the app was invoked) must not
        # hold its fd until some later GC pass.
        try:
            self._file.close()
        except Exception:  # noqa: BLE001 — finalizer: nothing useful to do
            pass

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        # Never let the server open the label by path (http.response.pathsend).
        extensions = {
            k: v for k, v in scope.get("extensions", {}).items() if k != "http.response.pathsend"
        }
        try:
            await super().__call__({**scope, "extensions": extensions}, receive, send)
        finally:
            self._file.close()  # a local fd close never blocks; safe under cancellation

    async def _read(self, size: int) -> bytes:
        return await anyio.to_thread.run_sync(self._file.read, size)

    async def _seek(self, offset: int) -> None:
        await anyio.to_thread.run_sync(self._file.seek, offset)

    async def _handle_simple(self, send: Send, send_header_only: bool, send_pathsend: bool) -> None:
        await send({"type": "http.response.start", "status": self.status_code, "headers": self.raw_headers})
        if send_header_only:
            await send({"type": "http.response.body", "body": b"", "more_body": False})
            return
        more_body = True
        while more_body:
            chunk = await self._read(self.chunk_size)
            more_body = len(chunk) == self.chunk_size
            await send({"type": "http.response.body", "body": chunk, "more_body": more_body})

    async def _handle_single_range(
        self, send: Send, start: int, end: int, file_size: int, send_header_only: bool
    ) -> None:
        self.headers["content-range"] = f"bytes {start}-{end - 1}/{file_size}"
        self.headers["content-length"] = str(end - start)
        await send({"type": "http.response.start", "status": 206, "headers": self.raw_headers})
        if send_header_only:
            await send({"type": "http.response.body", "body": b"", "more_body": False})
            return
        await self._seek(start)
        more_body = True
        while more_body:
            chunk = await self._read(min(self.chunk_size, end - start))
            start += len(chunk)
            more_body = len(chunk) == self.chunk_size and start < end
            await send({"type": "http.response.body", "body": chunk, "more_body": more_body})

    async def _handle_multiple_ranges(
        self,
        send: Send,
        ranges: list[tuple[int, int]],
        file_size: int,
        send_header_only: bool,
    ) -> None:
        from secrets import token_hex

        boundary = token_hex(13)
        content_length, header_generator = self.generate_multipart(
            ranges, boundary, file_size, self.headers["content-type"]
        )
        self.headers["content-range"] = f"multipart/byteranges; boundary={boundary}"
        self.headers["content-length"] = str(content_length)
        await send({"type": "http.response.start", "status": 206, "headers": self.raw_headers})
        if send_header_only:
            await send({"type": "http.response.body", "body": b"", "more_body": False})
            return
        for start, end in ranges:
            await send({"type": "http.response.body", "body": header_generator(start, end), "more_body": True})
            await self._seek(start)
            while start < end:
                chunk = await self._read(min(self.chunk_size, end - start))
                if not chunk:
                    break
                start += len(chunk)
                await send({"type": "http.response.body", "body": chunk, "more_body": True})
            await send({"type": "http.response.body", "body": b"\n", "more_body": True})
        await send(
            {
                "type": "http.response.body",
                "body": f"\n--{boundary}--\n".encode("latin-1"),
                "more_body": False,
            }
        )
