"""
@file_name: _confined_response.py
@author: Bin Liang
@date: 2026-10-30
@description: A Starlette ``FileResponse`` that serves an ALREADY-OPEN file
object instead of re-opening a path.

The raw artifact route used to hand ``FileResponse`` the resolved path string,
and Starlette opens it again when the first byte is sent. A tenant who swaps a
directory on that path for a symlink between resolution and sending made the
backend serve another tenant's file. The route now opens the file through
``utils/confined_io`` (every component O_NOFOLLOW) and gives this class the
resulting file object; headers (Content-Length / Last-Modified / ETag /
Accept-Ranges), HEAD, single and multi Range, If-Range and 416 behave exactly
as Starlette's because the framing logic is inherited — only the three body
writers that touch the file are replaced to read from the held object.
"""
from __future__ import annotations

import os
from typing import BinaryIO, Mapping, Optional

import anyio
from starlette.responses import FileResponse
from starlette.types import Receive, Scope, Send


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
        )

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
