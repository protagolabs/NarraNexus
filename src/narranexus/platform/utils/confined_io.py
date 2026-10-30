"""
@file_name: confined_io.py
@author: Bin Liang
@date: 2026-10-30
@description: fd-relative file operations that make "prove the path is inside
the root" and "open it" ONE operation.

Tenants control the contents of their own workspace and can create symlinks
there. Validating a path (realpath under the owner's roots) and then
re-opening it BY STRING leaves a window in which a directory component is
swapped for a link, so the open lands in another tenant's tree. This module
closes the window: every component below ``root`` is opened with
``O_NOFOLLOW`` relative to its parent's directory fd, and the leaf is opened
(or created / replaced) relative to the last directory fd. A link anywhere on
the path — planted at any moment, including after an earlier check — is
refused, never followed.

``root`` is the trust anchor: an absolute, platform-owned directory (typically
the realpath of ``base_working_path``). It is opened with ``O_NOFOLLOW`` too,
so it may not itself be a link, but ITS ancestors are resolved normally.
``rel`` is a POSIX path below it with no empty, ``.`` or ``..`` segment and no
NUL; everything the tenant can influence therefore sits inside ``rel``.

Fails closed: if the platform lacks ``dir_fd`` / ``O_NOFOLLOW`` support the
operations raise :class:`ConfinedIOUnsupported` rather than falling back to
the raceable string path. Linux and macOS (the desktop app) both qualify.
"""
from __future__ import annotations

import errno
import os
import secrets
import stat as stat_mod
from typing import BinaryIO, Optional

_O_CLOEXEC = getattr(os, "O_CLOEXEC", 0)
_DIR_FLAGS = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0) | _O_CLOEXEC
_MAX_SCAN_DEPTH = 64


class ConfinedPathError(ValueError):
    """The path is malformed or crosses a link: refuse, never fall back."""


class SymlinkRefusedError(ConfinedPathError):
    """A component of the path (or the root) is a symlink."""


class ConfinedIOUnsupported(ConfinedPathError):
    """The platform lacks the primitives needed to confine safely."""


class NotARegularFileError(OSError):
    """The leaf exists but is a directory, FIFO, device, ... — not servable."""


def _require_support() -> None:
    for name in ("O_NOFOLLOW", "O_DIRECTORY"):
        if not hasattr(os, name):
            raise ConfinedIOUnsupported(f"os.{name} is unavailable on this platform")
    # os.replace shares os.rename's dir_fd support (it is not listed on its own).
    for fn in (os.open, os.mkdir, os.unlink, os.rename, os.stat):
        if fn not in os.supports_dir_fd:
            raise ConfinedIOUnsupported(f"{fn.__name__} does not support dir_fd here")
    if os.stat not in os.supports_follow_symlinks:
        raise ConfinedIOUnsupported("os.stat does not support follow_symlinks=False here")
    if os.scandir not in os.supports_fd:
        raise ConfinedIOUnsupported("os.scandir does not support fd here")


def _split(rel: str, *, allow_empty: bool = False) -> list[str]:
    if not isinstance(rel, str) or "\x00" in rel:
        raise ConfinedPathError("invalid relative path")
    if rel == "":
        if allow_empty:
            return []
        raise ConfinedPathError("empty relative path")
    if rel.startswith("/"):
        raise ConfinedPathError(f"absolute path refused: {rel!r}")
    parts = rel.split("/")
    for p in parts:
        if p in ("", ".", ".."):
            raise ConfinedPathError(f"illegal path segment in {rel!r}")
    return parts


def _refuse_if_link(name: str, parent_fd: Optional[int], err: OSError, label: str) -> None:
    """Turn the errno O_NOFOLLOW produces on a link into SymlinkRefusedError."""
    if err.errno not in (errno.ELOOP, errno.ENOTDIR, errno.EMLINK):
        return
    try:
        if parent_fd is None:
            st = os.lstat(name)
        else:
            st = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
    except OSError:
        return
    if stat_mod.S_ISLNK(st.st_mode):
        raise SymlinkRefusedError(f"{label} is a symlink: {name!r}") from err


def _open_root(root: str) -> int:
    _require_support()
    if not isinstance(root, str) or not os.path.isabs(root) or "\x00" in root:
        raise ConfinedPathError("root must be an absolute path")
    try:
        return os.open(root, _DIR_FLAGS)
    except OSError as e:
        _refuse_if_link(root, None, e, "root")
        raise


def _open_dir_at(parent_fd: int, name: str, *, create: bool) -> int:
    try:
        return os.open(name, _DIR_FLAGS, dir_fd=parent_fd)
    except FileNotFoundError:
        if not create:
            raise
        try:
            os.mkdir(name, 0o755, dir_fd=parent_fd)
        except FileExistsError:
            pass
        try:
            return os.open(name, _DIR_FLAGS, dir_fd=parent_fd)
        except OSError as e:
            _refuse_if_link(name, parent_fd, e, "path component")
            raise
    except OSError as e:
        _refuse_if_link(name, parent_fd, e, "path component")
        raise


def _walk(root: str, parts: list[str], *, create: bool = False) -> int:
    """Open ``root`` then each of ``parts`` as directories; return the last fd."""
    fd = _open_root(root)
    try:
        for name in parts:
            nxt = _open_dir_at(fd, name, create=create)
            os.close(fd)
            fd = nxt
        return fd
    except BaseException:
        os.close(fd)
        raise


def open_dir_fd(root: str, rel: str = "") -> int:
    """Directory fd for ``root/rel`` (``rel`` may be empty). Caller closes it."""
    return _walk(root, _split(rel, allow_empty=True))


def make_dirs(root: str, rel: str) -> None:
    """Create ``root/rel`` (and missing parents) as REAL directories.

    Each existing component is opened with O_NOFOLLOW, so the creation never
    recurses through a link; a component that is a link raises
    SymlinkRefusedError and nothing is created beyond it.
    """
    os.close(_walk(root, _split(rel), create=True))


def open_read(root: str, rel: str) -> BinaryIO:
    """Open ``root/rel`` for binary reading; the leaf must be a regular file.

    Missing → FileNotFoundError. Link anywhere → SymlinkRefusedError. Directory /
    FIFO / device → NotARegularFileError. The leaf is opened O_NONBLOCK so a
    tenant-planted FIFO cannot hang the caller before the type check.
    """
    parts = _split(rel)
    parent_fd = _walk(root, parts[:-1])
    try:
        try:
            fd = os.open(
                parts[-1],
                os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | _O_CLOEXEC,
                dir_fd=parent_fd,
            )
        except OSError as e:
            _refuse_if_link(parts[-1], parent_fd, e, "leaf")
            raise
    finally:
        os.close(parent_fd)
    try:
        st = os.fstat(fd)
        if not stat_mod.S_ISREG(st.st_mode):
            raise NotARegularFileError(errno.EINVAL, "not a regular file", rel)
        os.set_blocking(fd, True)
        return os.fdopen(fd, "rb")
    except BaseException:
        # fdopen owns the fd only once it returns; close it ourselves otherwise.
        try:
            os.close(fd)
        except OSError:
            pass
        raise


def open_new(root: str, rel: str, *, mode: int = 0o644) -> BinaryIO:
    """Exclusively create ``root/rel`` and return it open for binary writing.

    O_EXCL|O_NOFOLLOW: an existing name — including a symlink, dangling or not —
    raises FileExistsError, so the write can never go through a planted link.
    """
    parts = _split(rel)
    parent_fd = _walk(root, parts[:-1])
    try:
        fd = os.open(
            parts[-1],
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | _O_CLOEXEC,
            mode,
            dir_fd=parent_fd,
        )
    finally:
        os.close(parent_fd)
    try:
        return os.fdopen(fd, "wb")
    except BaseException:
        os.close(fd)
        raise


def write_atomic(
    root: str,
    rel: str,
    data: bytes,
    *,
    mode: Optional[int] = None,
    make_parents: bool = False,
) -> None:
    """Atomically replace (or create) ``root/rel`` with ``data``.

    A temp file is created beside the leaf with O_CREAT|O_EXCL|O_NOFOLLOW via
    the parent's fd, fsynced, then renamed over the leaf with both dir fds — so
    readers see the old or the new bytes, never a partial file, and a link at
    the leaf is REPLACED rather than written through. ``mode`` defaults to the
    existing leaf's permission bits, else 0o644.
    """
    parts = _split(rel)
    parent_fd = _walk(root, parts[:-1], create=make_parents)
    tmp_name = f".narra_tmp_{secrets.token_hex(6)}"
    created = False
    try:
        if mode is None:
            mode = 0o644
            try:
                st = os.stat(parts[-1], dir_fd=parent_fd, follow_symlinks=False)
                if stat_mod.S_ISREG(st.st_mode):
                    mode = stat_mod.S_IMODE(st.st_mode)
            except FileNotFoundError:
                pass
        fd = os.open(
            tmp_name,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | _O_CLOEXEC,
            mode,
            dir_fd=parent_fd,
        )
        created = True
        with os.fdopen(fd, "wb") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
            try:
                os.fchmod(f.fileno(), mode)  # undo the umask so the bits are exact
            except OSError:
                pass
        os.replace(tmp_name, parts[-1], src_dir_fd=parent_fd, dst_dir_fd=parent_fd)
        created = False
    except BaseException:
        if created:
            try:
                os.unlink(tmp_name, dir_fd=parent_fd)
            except OSError:
                pass
        raise
    finally:
        os.close(parent_fd)


def unlink(root: str, rel: str) -> None:
    """Remove the file ``root/rel`` (a link leaf is removed itself, not followed)."""
    parts = _split(rel)
    parent_fd = _walk(root, parts[:-1])
    try:
        os.unlink(parts[-1], dir_fd=parent_fd)
    finally:
        os.close(parent_fd)


def lstat(root: str, rel: str) -> os.stat_result:
    """lstat of ``root/rel`` — directories on the way are NOFOLLOW-walked, the
    leaf is not followed."""
    parts = _split(rel)
    parent_fd = _walk(root, parts[:-1])
    try:
        return os.stat(parts[-1], dir_fd=parent_fd, follow_symlinks=False)
    finally:
        os.close(parent_fd)


def dir_regular_bytes(root: str, rel: str) -> int:
    """Recursive sum of REGULAR-file sizes under the directory ``root/rel``.

    Symlinks (to files or directories) contribute nothing and are never
    followed — ``getsize`` on a link reports the target's size, which would let
    a tenant read another tenant's file sizes through its own artifact row.
    Unreadable entries are skipped. Depth is capped.
    """
    start_fd = open_dir_fd(root, rel)

    def scan(fd: int, depth: int) -> int:
        total = 0
        try:
            with os.scandir(fd) as it:
                entries = list(it)
        except OSError:
            return 0
        for entry in entries:
            try:
                if entry.is_symlink():
                    continue
                if entry.is_dir(follow_symlinks=False):
                    if depth >= _MAX_SCAN_DEPTH:
                        continue
                    try:
                        child = os.open(entry.name, _DIR_FLAGS, dir_fd=fd)
                    except OSError:
                        continue
                    try:
                        total += scan(child, depth + 1)
                    finally:
                        os.close(child)
                elif entry.is_file(follow_symlinks=False):
                    total += entry.stat(follow_symlinks=False).st_size
            except OSError:
                continue
        return total

    try:
        return scan(start_fd, 0)
    finally:
        os.close(start_fd)
