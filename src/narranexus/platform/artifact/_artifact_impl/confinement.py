"""
@file_name: confinement.py
@author: Bin Liang
@date: 2026-10-28
@description: Pin a stored artifact ``file_path`` to the roots its OWNER may
touch before anything reads, writes or uploads next to it.

An artifact row's ``file_path`` is user-influenced: it arrives from a bundle
import, from an LLM tool argument, or from an older row written under a
previous layout. Serving / editing / asset-upload used to check only that the
resolved path sat under ``base_working_path`` — which is every tenant's root —
so a crafted pointer (``../<victim>/<agent>/x``) was "inside".

The admitted roots are exactly what the registration rules allow a writer to
produce: that agent's workspace (current nested layout plus the legacy flat
dir names that still hold real data on disk) and, for a team artifact, that
team's shared folder. Not the user's whole root: a sibling agent's tree would
otherwise be servable through the view-token raw route, which carries no JWT.

Roots are LEXICAL (``normpath``), never ``realpath``-ed: every one of those
directories is inside the tenant's own bind mount, so the tenant can replace
it with a symlink; resolving it would turn the link's target into "the
owner's root". The entry, on the other hand, IS realpath-ed, so a link anywhere
on its path lands it outside the lexical roots and fails closed.

``confine_entry`` is only the CHECK. Anything that then reads or writes the
entry must not re-open the returned string: between the check and the open a
directory on the path can be swapped for a link into another tenant's tree.
The ``*_confined`` helpers at the bottom perform the I/O itself through
``utils/fs/confined_io`` (every component below the base opened with
``O_NOFOLLOW`` relative to its parent fd), so a link planted at any moment is
refused instead of followed. 2026-10-30.
"""
from __future__ import annotations

import hashlib
import os
from typing import BinaryIO, Optional

from narranexus.platform.utils.fs import confined_io
from narranexus.platform.utils.fs.workspace_paths import (
    workspace_candidate_relpaths,
    resolve_workspace_relative_file,
    team_shared_dir,
)

from .errors import ArtifactPathEscape, ArtifactTooLarge


def owner_roots(
    agent_id: str, user_id: str, base: str, *, team_id: Optional[str] = None
) -> tuple[str, ...]:
    """Lexical roots an artifact of (agent_id, user_id[, team_id]) may live under."""
    base_real = os.path.realpath(base)
    roots = {
        os.path.normpath(os.path.join(base_real, rel))
        for rel in workspace_candidate_relpaths(agent_id, user_id)
    }
    if team_id:
        roots.add(os.path.normpath(str(team_shared_dir(user_id, team_id, base=base_real))))
    return tuple(sorted(roots))


def _within(path: str, root: str) -> bool:
    return path == root or path.startswith(root + os.sep)


def confine_entry(
    file_path: str, agent_id: str, user_id: str, base: str, *, team_id: Optional[str] = None
) -> str:
    """Resolve ``file_path`` (base-relative, layout-tolerant) and prove it sits
    under one of the owner's roots. Returns the entry's realpath.

    Raises ArtifactPathEscape otherwise — callers map it to their 404/410.
    A root that is itself a symlink is refused outright: a tenant swapping
    its workspace directory for a link is the attack this exists to stop.
    """
    if not file_path:
        raise ArtifactPathEscape("artifact has no content pointer")
    base_real = os.path.realpath(base)
    roots = owner_roots(agent_id, user_id, base_real, team_id=team_id)
    for root in roots:
        if os.path.islink(root):
            raise ArtifactPathEscape(f"owner root is a symlink: {root!r}")
    entry = os.path.realpath(str(resolve_workspace_relative_file(file_path, agent_id, user_id, base_real)))
    if not any(_within(entry, r) for r in roots):
        raise ArtifactPathEscape(f"entry outside owner roots: {file_path!r}")
    return entry


# ── confined I/O on a confine_entry result ────────────────────────────────────
#
# Every helper takes the ABSOLUTE path ``confine_entry`` / ``resolve_raw_file``
# produced and re-derives (base, rel) from it; the I/O then walks ``rel`` below
# ``base`` with O_NOFOLLOW. ``base`` is the realpath of ``base_working_path``
# (platform-owned), so everything a tenant can influence is inside ``rel``.
# ConfinedPathError (link on the path, malformed, platform unsupported) is
# mapped to ArtifactPathEscape; plain OSErrors (missing, not a regular file)
# pass through for the caller's 410 mapping.


def split_under_base(abs_path: str, base: Optional[str] = None) -> tuple[str, str]:
    """(realpath(base), POSIX path of ``abs_path`` below it), computed lexically.

    ``abs_path`` may spell the base either by its real path or as configured
    (relative / symlinked ``BASE_WORKING_PATH`` in local and desktop runs); the
    tenant tail is never resolved here. ArtifactPathEscape when ``abs_path`` is
    not strictly below the base.
    """
    if base is None:
        from narranexus.platform.settings import settings
        base = settings.base_working_path
    try:
        return confined_io.split_under_base(abs_path, base)
    except confined_io.ConfinedPathError as e:
        raise ArtifactPathEscape(str(e)) from e


def open_confined_read(abs_path: str, base: Optional[str] = None) -> BinaryIO:
    """Open a confined entry/asset for binary reading (regular files only)."""
    base_real, rel = split_under_base(abs_path, base)
    try:
        return confined_io.open_read(base_real, rel)
    except confined_io.ConfinedPathError as e:
        raise ArtifactPathEscape(str(e)) from e


def confined_stat(abs_path: str, base: Optional[str] = None) -> os.stat_result:
    """fstat of one confined regular file (size / mtime of the inode that was
    opened through the link-free walk)."""
    with open_confined_read(abs_path, base) as f:
        return os.fstat(f.fileno())


def confined_file_size(abs_path: str, base: Optional[str] = None) -> int:
    """Size of one confined regular file, measured on the fd it was opened as."""
    return confined_stat(abs_path, base).st_size


def sha256_of_confined(abs_path: str, base: Optional[str] = None) -> str:
    """sha256 hex of one confined regular file, streamed in 1 MB chunks off the
    fd it was opened as (the hash covers exactly the inode that was proven)."""
    digest = hashlib.sha256()
    with open_confined_read(abs_path, base) as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def write_confined_atomic(abs_path: str, data: bytes, base: Optional[str] = None) -> None:
    """Atomically replace a confined file's bytes (temp + rename via dir fds)."""
    base_real, rel = split_under_base(abs_path, base)
    try:
        confined_io.write_atomic(base_real, rel, data)
    except confined_io.ConfinedPathError as e:
        raise ArtifactPathEscape(str(e)) from e


def confined_dir_size(abs_dir: str, base: Optional[str] = None) -> int:
    """Recursive regular-file byte total under a confined directory (links are
    neither followed nor counted)."""
    base_real, rel = split_under_base(abs_dir, base)
    try:
        return confined_io.dir_regular_bytes(base_real, rel)
    except confined_io.SymlinkRefusedError as e:
        raise ArtifactPathEscape(str(e)) from e
    except confined_io.ConfinedScanError as e:
        # Not a path escape: the tree is too deep / unreadable to be measured, so
        # its size cannot be vouched for against the artifact limit — refuse it
        # with its own message (413 like any over-limit artifact).
        raise ArtifactTooLarge(f"artifact directory cannot be measured, so it is refused: {e}") from e
    except confined_io.ConfinedPathError as e:
        raise ArtifactPathEscape(str(e)) from e


def confined_path_exists(abs_path: str, base: Optional[str] = None) -> bool:
    """True when ``abs_path`` exists (as anything but a followed link) under a
    link-free path. A link on the way reads as absent, never as a peek."""
    try:
        base_real, rel = split_under_base(abs_path, base)
        confined_io.lstat(base_real, rel)
        return True
    except (OSError, confined_io.ConfinedPathError, ArtifactPathEscape):
        return False
