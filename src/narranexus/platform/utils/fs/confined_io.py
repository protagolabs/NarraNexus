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
from typing import BinaryIO, NamedTuple, Optional

_O_CLOEXEC = getattr(os, "O_CLOEXEC", 0)
_DIR_FLAGS = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0) | _O_CLOEXEC
_MAX_SCAN_DEPTH = 64


class ConfinedPathError(ValueError):
    """The path is malformed or crosses a link: refuse, never fall back."""


class SymlinkRefusedError(ConfinedPathError):
    """A component of the path (or the root) is a symlink."""


class ConfinedIOUnsupported(ConfinedPathError):
    """The platform lacks the primitives needed to confine safely."""


class ConfinedScanError(ConfinedPathError):
    """A recursive scan could not be completed (too deep / unreadable
    directory). Callers that enforce a size limit must refuse, not undercount."""


class TreeTooDeepError(ConfinedScanError):
    """A directory tree is deeper than the recursion cap of the scan / copy
    that met it (or exhausted the process's file-descriptor budget)."""


class NotARegularFileError(OSError):
    """The leaf exists but is a directory, FIFO, device, ... — not servable."""


def _require_support() -> None:
    for name in ("O_NOFOLLOW", "O_DIRECTORY"):
        if not hasattr(os, name):
            raise ConfinedIOUnsupported(f"os.{name} is unavailable on this platform")
    # os.replace shares os.rename's dir_fd support (it is not listed on its own).
    for fn in (os.open, os.mkdir, os.unlink, os.rmdir, os.rename, os.stat):
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


def split_under_base(path: str, base: str) -> tuple[str, str]:
    """(root, rel) for ``path`` strictly below ``base``, computed LEXICALLY.

    ``root`` is ``realpath(base)`` — the platform-owned trust anchor handed to
    the other functions here. ``path`` is only normalised with ``abspath``
    (collapses ``..`` and relative prefixes; never follows a link) and may name
    the base either by its real path or by the configured spelling (relative or
    symlinked ``BASE_WORKING_PATH`` in local / desktop runs). The tenant tail is
    never resolved here — it is walked O_NOFOLLOW by the callers. Raises
    ConfinedPathError when ``path`` is not strictly below the base.
    """
    if not isinstance(path, str) or not isinstance(base, str) or "\x00" in path:
        raise ConfinedPathError("invalid path")
    root = os.path.realpath(base)
    p = os.path.abspath(path)
    for prefix in (root, os.path.abspath(base)):
        if p.startswith(prefix + os.sep):
            return root, p[len(prefix) + 1:]
    raise ConfinedPathError(f"path is outside the workspace base: {path!r}")


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
    """Open ``root`` then each of ``parts`` as directories; return the last fd.

    With ``create`` the (platform-owned) root itself is created first when it
    does not exist yet — a fresh install has no workspace base on disk — and
    every missing component below it is created as a real directory."""
    if create and isinstance(root, str) and os.path.isabs(root) and "\x00" not in root:
        _require_support()
        os.makedirs(root, exist_ok=True)
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


def _open_regular_at(parent_fd: int, name: str, label: str) -> BinaryIO:
    """Open the regular file ``name`` relative to the directory fd (O_NOFOLLOW)."""
    try:
        fd = os.open(
            name,
            os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | _O_CLOEXEC,
            dir_fd=parent_fd,
        )
    except OSError as e:
        _refuse_if_link(name, parent_fd, e, "leaf")
        raise
    try:
        st = os.fstat(fd)
        if not stat_mod.S_ISREG(st.st_mode):
            raise NotARegularFileError(errno.EINVAL, "not a regular file", label)
        os.set_blocking(fd, True)
        return os.fdopen(fd, "rb")
    except BaseException:
        # fdopen owns the fd only once it returns; close it ourselves otherwise.
        try:
            os.close(fd)
        except OSError:
            pass
        raise


def open_read(root: str, rel: str) -> BinaryIO:
    """Open ``root/rel`` for binary reading; the leaf must be a regular file.

    Missing → FileNotFoundError. Link anywhere → SymlinkRefusedError. Directory /
    FIFO / device → NotARegularFileError. The leaf is opened O_NONBLOCK so a
    tenant-planted FIFO cannot hang the caller before the type check.
    """
    parts = _split(rel)
    parent_fd = _walk(root, parts[:-1])
    try:
        return _open_regular_at(parent_fd, parts[-1], rel)
    finally:
        os.close(parent_fd)


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
                    # Permission bits only: never carry setuid/setgid/sticky
                    # onto the replacement.
                    mode = stat_mod.S_IMODE(st.st_mode) & 0o777
            except FileNotFoundError:
                pass
        fd = os.open(
            tmp_name,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | _O_CLOEXEC,
            mode,
            dir_fd=parent_fd,
        )
        created = True
        try:
            f = os.fdopen(fd, "wb")
        except BaseException:
            os.close(fd)  # fdopen owns the fd only once it returns
            raise
        with f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
            try:
                os.fchmod(f.fileno(), mode & 0o777)  # undo the umask so the bits are exact
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


class DirEntryInfo(NamedTuple):
    """One directory entry as seen WITHOUT following links: ``st`` is an lstat."""

    name: str
    st: os.stat_result

    @property
    def is_dir(self) -> bool:
        return stat_mod.S_ISDIR(self.st.st_mode)

    @property
    def is_file(self) -> bool:
        return stat_mod.S_ISREG(self.st.st_mode)

    @property
    def is_link(self) -> bool:
        return stat_mod.S_ISLNK(self.st.st_mode)


def list_dir(root: str, rel: str = "") -> list[DirEntryInfo]:
    """List ``root/rel`` (``rel`` may be empty) with per-entry lstat.

    The directory is reached by the link-free walk and scanned through its fd;
    every entry is described by ``lstat`` relative to that fd, so a link entry
    is reported AS A LINK (its own size/mtime), never as its target — a planted
    link cannot make a listing disclose another tenant's names, sizes or
    mtimes. Entries that vanish mid-scan are skipped; unordered.
    Missing dir → FileNotFoundError; a file → NotADirectoryError; a link on the
    path → SymlinkRefusedError.
    """
    fd = open_dir_fd(root, rel)
    try:
        return list_fd(fd)
    finally:
        os.close(fd)


def list_fd(fd: int) -> list[DirEntryInfo]:
    """``list_dir`` for a directory fd the caller already holds (from
    :func:`open_dir_fd` / :func:`open_subdir`): scandir on the fd, per-entry
    lstat, vanished entries skipped, unordered. The fd stays open."""
    with os.scandir(fd) as it:
        entries = list(it)
    out: list[DirEntryInfo] = []
    for entry in entries:
        try:
            out.append(DirEntryInfo(entry.name, entry.stat(follow_symlinks=False)))
        except OSError:
            continue
    return out


def open_subdir(parent_fd: int, name: str) -> int:
    """Open the child directory ``name`` relative to ``parent_fd`` without
    following a link — one ``openat``, no re-walk from the root. For callers
    that recurse a tree themselves while holding one fd per level (a listing
    with its own budget / depth rules that ``walk_tree`` does not express).
    A link → SymlinkRefusedError; missing → FileNotFoundError; the caller
    closes the returned fd."""
    _require_support()
    if not name or name in (".", "..") or "/" in name or "\x00" in name:
        raise ConfinedPathError(f"invalid directory name {name!r}")
    return _open_dir_at(parent_fd, name, create=False)


#: Hard cap on the depth ``walk_tree`` / ``iter_tree`` descend (one held fd per
#: level). A quarter of macOS's default soft RLIMIT_NOFILE (256), so a walk
#: leaves room for concurrent requests; deeper trees raise TreeTooDeepError
#: instead of silently dropping content.
MAX_TREE_DEPTH = 64


class TreeNode:
    """One entry of :func:`walk_tree`. ``open_file()`` opens THIS regular file
    relative to the directory fd the walk is holding (one ``openat``, no re-walk
    from the root) and is valid only until the iteration advances."""

    __slots__ = ("rel", "entry", "_dir_fd")

    def __init__(self, rel: str, entry: "DirEntryInfo", dir_fd: int) -> None:
        self.rel = rel
        self.entry = entry
        self._dir_fd = dir_fd

    @property
    def is_dir(self) -> bool:
        return self.entry.is_dir

    def open_file(self) -> BinaryIO:
        if not self.entry.is_file:
            raise NotARegularFileError(errno.EINVAL, "not a regular file", self.rel)
        return _open_regular_at(self._dir_fd, self.entry.name, self.rel)


def walk_tree(
    root: str,
    rel: str = "",
    *,
    skip=None,
    recurse: bool = True,
    strict: bool = False,
    max_depth: int = MAX_TREE_DEPTH,
):
    """Walk the directory ``root/rel`` fd-relative in LINEAR time, yielding a
    :class:`TreeNode` for every directory and regular file.

    The start directory is reached once by the link-free walk; below it the walk
    holds ONE open directory fd per level and opens each child / file relative to
    its parent's fd (``openat``) — never a re-walk from the root. Links, FIFOs,
    sockets and devices are never yielded. ``skip(rel, is_dir)`` prunes an entry
    and, for a directory, its whole subtree. Directories are yielded before
    their children, names in sorted order; subdirectories are then entered in
    sorted order. A tree deeper than ``max_depth`` raises TreeTooDeepError —
    always, never a silent skip, since callers (exports, hashes, size limits) rely
    on completeness. A subdirectory that cannot be entered (swapped for a link,
    unreadable) is skipped with a WARNING, or raises with ``strict=True``; the
    start directory failing always raises.
    """
    from loguru import logger

    start_fd = open_dir_fd(root, rel)
    # frame: (dir fd, rel_dir, pending subdirs (reversed), depth)
    stack: list = []
    try:
        def open_frame(fd: int, rel_dir: str, depth: int, nodes_out: list) -> None:
            with os.scandir(fd) as it:
                entries = list(it)
            infos = []
            for e in entries:
                try:
                    infos.append(DirEntryInfo(e.name, e.stat(follow_symlinks=False)))
                except OSError:
                    continue
            subdirs = []
            for info in sorted(infos, key=lambda i: i.name):
                child = f"{rel_dir}/{info.name}" if rel_dir else info.name
                if info.is_dir:
                    if skip is not None and skip(child, True):
                        continue
                    nodes_out.append(TreeNode(child, info, fd))
                    subdirs.append((info.name, child))
                elif info.is_file:
                    if skip is not None and skip(child, False):
                        continue
                    nodes_out.append(TreeNode(child, info, fd))
            stack.append([fd, subdirs[::-1] if recurse else [], depth])

        nodes: list = []
        open_frame(start_fd, "", 0, nodes)
        yield from nodes
        while stack:
            frame = stack[-1]
            fd, pending, depth = frame[0], frame[1], frame[2]
            if not pending:
                stack.pop()
                if fd != start_fd:
                    os.close(fd)
                continue
            name, child_rel = pending.pop()
            if depth + 1 > max_depth:
                raise TreeTooDeepError(f"directory tree deeper than {max_depth} levels at {child_rel!r}")
            try:
                child_fd = _open_dir_at(fd, name, create=False)
            except FileNotFoundError:
                continue
            except (OSError, ConfinedPathError) as e:
                if strict:
                    raise
                logger.warning(f"tree walk: {child_rel!r} skipped (not enterable): {e}")
                continue
            nodes = []
            try:
                open_frame(child_fd, child_rel, depth + 1, nodes)
            except OSError as e:
                os.close(child_fd)
                if strict:
                    raise
                logger.warning(f"tree walk: {child_rel!r} skipped (unreadable): {e}")
                continue
            yield from nodes
    finally:
        for frame in stack:
            if frame[0] != start_fd:
                try:
                    os.close(frame[0])
                except OSError:
                    pass
        try:
            os.close(start_fd)
        except OSError:
            pass


def iter_tree(
    root: str,
    rel: str = "",
    *,
    skip=None,
    recurse: bool = True,
    strict: bool = False,
    max_depth: int = MAX_TREE_DEPTH,
):
    """``walk_tree`` yielding ``(rel_in_tree, DirEntryInfo)`` — for callers that
    only need the listing (the file bytes, if needed, are opened afterwards)."""
    for node in walk_tree(root, rel, skip=skip, recurse=recurse, strict=strict, max_depth=max_depth):
        yield node.rel, node.entry


def dir_regular_bytes(root: str, rel: str) -> int:
    """Recursive sum of REGULAR-file sizes under the directory ``root/rel``.

    Symlinks (to files or directories) contribute nothing and are never
    followed — ``getsize`` on a link reports the target's size, which would let
    a tenant read another tenant's file sizes through its own artifact row.

    Fails closed: a tree deeper than the cap, an unreadable directory, or a
    directory swapped for a link mid-scan raises ConfinedScanError /
    SymlinkRefusedError instead of undercounting — callers use the total to
    enforce a size limit, and a silent skip would let a tenant hide bytes below
    a deep or unreadable directory. Entries that vanish mid-scan are skipped.
    """
    start_fd = open_dir_fd(root, rel)

    def scan(fd: int, depth: int) -> int:
        total = 0
        try:
            with os.scandir(fd) as it:
                entries = list(it)
        except OSError as e:
            raise ConfinedScanError(f"directory not readable during size scan: {e}") from e
        for entry in entries:
            try:
                if entry.is_symlink():
                    continue
                if entry.is_dir(follow_symlinks=False):
                    if depth >= _MAX_SCAN_DEPTH:
                        raise TreeTooDeepError(
                            f"directory tree deeper than {_MAX_SCAN_DEPTH} levels"
                        )
                    try:
                        child = _open_dir_at(fd, entry.name, create=False)
                    except FileNotFoundError:
                        continue
                    except SymlinkRefusedError:
                        raise
                    except OSError as e:
                        raise ConfinedScanError(f"directory not readable during size scan: {e}") from e
                    try:
                        total += scan(child, depth + 1)
                    finally:
                        os.close(child)
                elif entry.is_file(follow_symlinks=False):
                    total += entry.stat(follow_symlinks=False).st_size
            except FileNotFoundError:
                continue
        return total

    try:
        return scan(start_fd, 0)
    finally:
        os.close(start_fd)


def split_source(path: str, base: str) -> tuple[str, str]:
    """(root, rel) for a copy SOURCE directory.

    Below the workspace base → ``split_under_base`` (so the source is walked
    O_NOFOLLOW from the base too: a source inside a tenant's own tree must not
    be redirected by a swapped link). Otherwise (platform-owned staging such as
    a ``mkdtemp`` clone / unzip dir) → (realpath(parent), leaf): the parent is
    platform-owned and trusted, the leaf itself is opened O_NOFOLLOW.
    """
    try:
        return split_under_base(path, base)
    except ConfinedPathError:
        p = os.path.abspath(path)
        parent, leaf = os.path.split(p)
        if not leaf:
            raise
        return os.path.realpath(parent), leaf


def rename_within(root: str, src_rel: str, dst_rel: str, *, make_dst_parents: bool = False) -> None:
    """Rename ``root/src_rel`` to ``root/dst_rel`` through BOTH parents' fds.

    Neither parent chain is followed through a link (O_NOFOLLOW per component),
    and the rename itself is ``renameat`` on the two directory fds, so a swapped
    directory can neither receive the moved entry nor lose one that was never
    ours. A link leaf is renamed as a link, never followed. A non-empty
    destination directory raises FileExistsError (nothing is merged or nested).

    Destination semantics are those of ``renameat``: an existing FILE
    destination is replaced atomically (the source must be a file too — a
    directory over a file raises NotADirectoryError); an existing EMPTY
    directory destination is replaced (the source must be a directory — a file
    over a directory raises IsADirectoryError).
    """
    src_parts = _split(src_rel)
    dst_parts = _split(dst_rel)
    src_fd = _walk(root, src_parts[:-1])
    try:
        dst_fd = _walk(root, dst_parts[:-1], create=make_dst_parents)
        try:
            try:
                os.rename(src_parts[-1], dst_parts[-1], src_dir_fd=src_fd, dst_dir_fd=dst_fd)
            except OSError as e:
                if e.errno in (errno.ENOTEMPTY, errno.EEXIST):
                    raise FileExistsError(e.errno, "destination exists", dst_rel) from e
                raise
        finally:
            os.close(dst_fd)
    finally:
        os.close(src_fd)


def link_or_copy(
    src_root: str,
    src_rel: str,
    dst_root: str,
    dst_rel: str,
    *,
    make_dst_parents: bool = False,
) -> None:
    """Hard-link the regular file ``src_root/src_rel`` to the NEW name
    ``dst_root/dst_rel``, falling back to a byte copy (cross-device, or a
    filesystem without hard links).

    Both parent chains are walked O_NOFOLLOW and the link / copy is done
    relative to the two directory fds; the source leaf must be a regular file
    (a link or special file raises NotARegularFileError — ``linkat`` would
    otherwise link the symlink inode itself). The destination must not exist
    (FileExistsError) and is created ``O_EXCL|O_NOFOLLOW``. The SOURCE must be
    the caller's own tree or platform staging, never another tenant's.
    """
    sparts = _split(src_rel)
    dparts = _split(dst_rel)
    sfd = _walk(src_root, sparts[:-1])
    try:
        dfd = _walk(dst_root, dparts[:-1], create=make_dst_parents)
        try:
            st = os.stat(sparts[-1], dir_fd=sfd, follow_symlinks=False)
            if not stat_mod.S_ISREG(st.st_mode):
                raise NotARegularFileError(errno.EINVAL, "not a regular file", src_rel)
            try:
                os.link(sparts[-1], dparts[-1], src_dir_fd=sfd, dst_dir_fd=dfd, follow_symlinks=False)
            except FileExistsError:
                raise
            except (OSError, NotImplementedError):  # EXDEV, no hard links, no dir_fd link
                _copy_file_fds(sfd, sparts[-1], dfd, dparts[-1])
                return
            # The source may have been swapped for a link between the lstat and
            # the link: never leave a hard link to a symlink inode behind.
            if not stat_mod.S_ISREG(os.stat(dparts[-1], dir_fd=dfd, follow_symlinks=False).st_mode):
                os.unlink(dparts[-1], dir_fd=dfd)
                raise NotARegularFileError(errno.EINVAL, "source changed type", src_rel)
        finally:
            os.close(dfd)
    finally:
        os.close(sfd)


def _copy_file_fds(sfd: int, sname: str, dfd: int, dname: str) -> None:
    in_fd = os.open(sname, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | _O_CLOEXEC, dir_fd=sfd)
    try:
        st = os.fstat(in_fd)
        if not stat_mod.S_ISREG(st.st_mode):
            raise NotARegularFileError(errno.EINVAL, "not a regular file", sname)
        os.set_blocking(in_fd, True)
        mode = stat_mod.S_IMODE(st.st_mode) & 0o777
        out_fd = os.open(
            dname, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | _O_CLOEXEC, mode, dir_fd=dfd
        )
        try:
            while chunk := os.read(in_fd, 1024 * 1024):
                view = memoryview(chunk)
                while view:
                    view = view[os.write(out_fd, view):]
            os.fchmod(out_fd, mode)
        except BaseException:
            os.close(out_fd)
            out_fd = -1
            try:
                os.unlink(dname, dir_fd=dfd)
            except OSError:
                pass
            raise
        finally:
            if out_fd >= 0:
                os.close(out_fd)
    finally:
        os.close(in_fd)


def copy_tree(
    src_root: str,
    src_rel: str,
    dst_root: str,
    dst_rel: str,
    *,
    make_parents: bool = False,
    max_depth: int = 64,
) -> None:
    """Copy the directory ``src_root/src_rel`` to the NEW directory
    ``dst_root/dst_rel`` without following a link on either side.

    The SOURCE must be platform-owned staging (a fresh clone / unzip dir) or
    another directory of the SAME tenant — never a path a different tenant
    controls; it is still walked O_NOFOLLOW (see ``split_source``), and links
    inside it are copied AS LINKS (``readlink`` + ``symlink`` via dir fds), never
    dereferenced. The DESTINATION is created through dir fds: ``mkdir`` /
    ``O_CREAT|O_EXCL|O_NOFOLLOW`` per entry, so nothing can be written through a
    link planted there. Regular files keep their permission bits (``& 0o777``);
    FIFOs, sockets and devices are skipped. The destination must not exist
    (FileExistsError). On any failure the partial destination is removed.
    """
    src_fd = open_dir_fd(src_root, src_rel)
    try:
        parts = _split(dst_rel)
        parent_fd = _walk(dst_root, parts[:-1], create=make_parents)
        try:
            os.mkdir(parts[-1], 0o755, dir_fd=parent_fd)
        finally:
            os.close(parent_fd)
        try:
            dst_fd = open_dir_fd(dst_root, dst_rel)
            try:
                _copy_dir_fds(src_fd, dst_fd, 0, max_depth)
            finally:
                os.close(dst_fd)
        except BaseException:
            try:
                remove_tree(dst_root, dst_rel)
            except (OSError, ConfinedPathError):
                pass
            raise
    finally:
        os.close(src_fd)


def _copy_dir_fds(src_fd: int, dst_fd: int, depth: int, max_depth: int, merge: bool = False) -> None:
    if depth > max_depth:
        raise TreeTooDeepError(f"directory tree deeper than {max_depth} levels")
    with os.scandir(src_fd) as it:
        entries = list(it)
    for entry in entries:
        name = entry.name
        if merge:
            _clear_conflict(dst_fd, name, want_dir=entry.is_dir(follow_symlinks=False))
        if entry.is_symlink():
            os.symlink(os.readlink(name, dir_fd=src_fd), name, dir_fd=dst_fd)
        elif entry.is_dir(follow_symlinks=False):
            child_src = _open_dir_at(src_fd, name, create=False)
            try:
                try:
                    os.mkdir(name, 0o755, dir_fd=dst_fd)
                except FileExistsError:
                    if not merge:
                        raise
                child_dst = _open_dir_at(dst_fd, name, create=False)
                try:
                    _copy_dir_fds(child_src, child_dst, depth + 1, max_depth, merge)
                finally:
                    os.close(child_dst)
            finally:
                os.close(child_src)
        elif entry.is_file(follow_symlinks=False):
            in_fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | _O_CLOEXEC, dir_fd=src_fd)
            try:
                st = os.fstat(in_fd)
                if not stat_mod.S_ISREG(st.st_mode):
                    continue
                os.set_blocking(in_fd, True)
                mode = stat_mod.S_IMODE(st.st_mode) & 0o777
                # merge: write a private temp then rename over the name, so a
                # file (or link) already there is REPLACED, never written through
                out_name = f".narra_tmp_{secrets.token_hex(6)}" if merge else name
                out_fd = os.open(
                    out_name,
                    os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | _O_CLOEXEC,
                    mode,
                    dir_fd=dst_fd,
                )
                try:
                    while chunk := os.read(in_fd, 1024 * 1024):
                        view = memoryview(chunk)
                        while view:
                            view = view[os.write(out_fd, view):]
                    os.fchmod(out_fd, mode)
                    if merge:
                        os.rename(out_name, name, src_dir_fd=dst_fd, dst_dir_fd=dst_fd)
                except BaseException:
                    if merge:
                        try:
                            os.unlink(out_name, dir_fd=dst_fd)
                        except OSError:
                            pass
                    raise
                finally:
                    os.close(out_fd)
            finally:
                os.close(in_fd)
        # sockets / FIFOs / devices are not part of the tree: skipped


def _clear_conflict(dst_fd: int, name: str, *, want_dir: bool) -> None:
    """Merge mode: remove a destination entry whose TYPE conflicts with the
    incoming one (a link or file where a directory must go, a directory or link
    where a file must go is handled by the temp+rename). A link is unlinked
    itself, never followed."""
    try:
        st = os.stat(name, dir_fd=dst_fd, follow_symlinks=False)
    except FileNotFoundError:
        return
    if want_dir and not stat_mod.S_ISDIR(st.st_mode):
        os.unlink(name, dir_fd=dst_fd)
    elif not want_dir and stat_mod.S_ISDIR(st.st_mode):
        # a directory where a file/link must go: refuse rather than delete a tree
        raise IsADirectoryError(errno.EISDIR, "destination is a directory", name)


def merge_tree(
    src_root: str,
    src_rel: str,
    dst_root: str,
    dst_rel: str,
    *,
    max_depth: int = 64,
) -> None:
    """Copy the CONTENTS of the directory ``src_root/src_rel`` into the directory
    ``dst_root/dst_rel`` (created, with parents, if missing), merging into what
    is already there and replacing same-named files.

    The destination path is walked O_NOFOLLOW from ``dst_root`` creating missing
    components, so a symlink anywhere on it — a tenant pre-creating
    ``{user}/{new_agent}`` as a link into another tenant's tree — is refused with
    SymlinkRefusedError before anything is written. Inside, a file replaces a
    same-named file or link through a private temp + rename (never written
    through); a link where a directory must go is unlinked; a directory where a
    file must go raises IsADirectoryError. Links in the source are copied as links.
    The SOURCE must be platform-owned staging (e.g. a fresh extraction dir).
    """
    src_fd = open_dir_fd(src_root, src_rel)
    try:
        dst_fd = _walk(dst_root, _split(dst_rel), create=True)
        try:
            _copy_dir_fds(src_fd, dst_fd, 0, max_depth, merge=True)
        finally:
            os.close(dst_fd)
    finally:
        os.close(src_fd)


def _remove_depth_cap() -> int:
    """Depth ``remove_tree`` may descend: a quarter of the process's fd soft
    limit (so macOS's 256 gives 64, Linux's usual 1024 gives 256), never above
    4096 — decided BEFORE any fd is opened for a level, so the delete stops with
    TreeTooDeepError well ahead of EMFILE (which would starve every other
    request in this process)."""
    try:
        import resource

        soft = resource.getrlimit(resource.RLIMIT_NOFILE)[0]
        if soft == resource.RLIM_INFINITY or soft < 0:
            soft = 1 << 16
    except (ImportError, ValueError, OSError):
        soft = 256
    return max(8, min(soft // 4, 4096))


def remove_tree(root: str, rel: str) -> None:
    """Delete the file, link or directory tree at ``root/rel`` without ever
    following a link.

    Every directory on the way to the parent is opened O_NOFOLLOW; below it the
    tree is removed with ``unlink`` / ``rmdir`` relative to directory fds. A
    symlink ANYWHERE inside the tree (or as the leaf) is unlinked itself — its
    target is never touched, so a link planted to another tenant's tree costs
    that tenant nothing. An entry that changes type under the walk is handled
    where it stands: a directory swapped for a link or a regular file between
    the listing and the open is UNLINKED (the link / file itself), at the top
    level and nested alike, so a tenant cannot leave its own tree behind by
    racing the delete. Iterative (no recursion): one open fd per level, so the
    depth is capped at ``_remove_depth_cap()`` (a quarter of the fd soft limit) —
    beyond it TreeTooDeepError is raised BEFORE the fd budget is touched, with
    the tree left partially intact. Missing leaf → FileNotFoundError.
    """
    parts = _split(rel)
    parent_fd = _walk(root, parts[:-1])
    try:
        name = parts[-1]
        st = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
        if not stat_mod.S_ISDIR(st.st_mode):
            os.unlink(name, dir_fd=parent_fd)
            return
        try:
            top_fd = _open_dir_at(parent_fd, name, create=False)
        except (SymlinkRefusedError, NotADirectoryError):
            os.unlink(name, dir_fd=parent_fd)  # swapped since the stat: remove it as it stands
            return
        _remove_dir_contents(top_fd)
        os.rmdir(name, dir_fd=parent_fd)
    finally:
        os.close(parent_fd)


def _remove_dir_contents(top_fd: int) -> None:
    """Remove everything below the open directory ``top_fd`` (closes it).

    Explicit stack of ``(fd, pending entries, name-in-parent)`` frames; a
    finished child is ``rmdir``-ed through the previous frame's fd, so nothing
    holds more than one fd per level.
    """

    def _entries(fd: int) -> list:
        with os.scandir(fd) as it:
            return list(it)

    depth_cap = _remove_depth_cap()
    try:
        top_entries = _entries(top_fd)
    except BaseException:
        os.close(top_fd)  # the docstring's "closes it" holds on every path
        raise
    stack = [(top_fd, top_entries, None)]
    try:
        while stack:
            fd, entries, child_name = stack[-1]
            if not entries:
                stack.pop()
                os.close(fd)
                if stack:
                    os.rmdir(child_name, dir_fd=stack[-1][0])
                continue
            entry = entries.pop()
            if not entry.is_dir(follow_symlinks=False):
                try:
                    os.unlink(entry.name, dir_fd=fd)  # file, or the link itself
                except FileNotFoundError:
                    pass
                continue
            if len(stack) >= depth_cap:
                raise TreeTooDeepError(
                    f"directory tree deeper than {depth_cap} levels "
                    "(capped well below the process file-descriptor limit)"
                )
            try:
                child = _open_dir_at(fd, entry.name, create=False)
            except FileNotFoundError:
                continue
            except (SymlinkRefusedError, NotADirectoryError):
                # swapped for a link / regular file since the listing: remove
                # that link / file itself, never its target
                try:
                    os.unlink(entry.name, dir_fd=fd)
                except FileNotFoundError:
                    pass
                continue
            except OSError as e:
                if e.errno in (errno.EMFILE, errno.ENFILE):
                    raise TreeTooDeepError(
                        "directory tree too deep for the process file-descriptor limit"
                    ) from e
                raise
            try:
                child_entries = _entries(child)
            except OSError as e:
                os.close(child)
                if e.errno in (errno.EMFILE, errno.ENFILE):
                    raise TreeTooDeepError(
                        "directory tree too deep for the process file-descriptor limit"
                    ) from e
                raise
            stack.append((child, child_entries, entry.name))
    except BaseException:
        for fd, _entries_left, _name in stack:
            try:
                os.close(fd)
            except OSError:
                pass
        raise
