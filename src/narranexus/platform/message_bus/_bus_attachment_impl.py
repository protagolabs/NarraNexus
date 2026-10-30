"""
@file_name: _bus_attachment_impl.py
@author: NarraNexus
@date: 2026-07-20
@description: Staging + marker synthesis for files attached to bus messages.

The message bus carries multimodal content by REFERENCE, never by embedding
bytes. When an agent attaches a file to a bus message it names a handle:

  * an attachment ``file_id`` (``att_...``) — a file it received earlier
    (user upload / another agent's bus message), or
  * a workspace-relative path — a file it produced itself (an artifact,
    a report it just wrote).

On send we resolve the handle to a concrete source file inside the SENDER's
own workspace, then stage the bytes into the OWNER's per-user shared area
(``{base}/{user_id}/_shared/bus_files/{date}/``) with ``os.link`` (hard-link,
zero-copy on the same filesystem; copy fallback across devices). The bus
message stores only a small dict per file (metadata + a base-relative path).

Why the shared area works for delivery without a per-recipient copy: the bus
forbids cross-user messaging, and the per-user Executor bind-mounts the whole
``{base}/{user_id}`` subtree — so every same-user recipient can ``Read`` the
staged path in both local and cloud mode. See ``utils/fs/workspace_paths.py``.

Security: ``sender_agent_id`` / ``owner_user_id`` come from authenticated
runtime context, never from the LLM. Workspace-relative handles are resolved
and checked to stay inside the sender's workspace (no ``../`` escape).
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import mimetypes
import os
import stat
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import List, NamedTuple, Optional

from loguru import logger

from narranexus.platform.repository.team_workspace_repository import TeamFileRepository
from narranexus.platform.schema.attachment_schema import derive_category_from_mime, file_marker
from narranexus.platform.utils.attachment_storage import (
    generate_file_id,
    is_valid_file_id,
    on_disk_suffix,
    resolve_attachment_path,
)
from narranexus.platform.utils.fs import confined_io
from narranexus.platform.utils.fs.file_safety import sanitize_filename
from narranexus.platform.utils.fs.workspace_paths import (
    agent_workspace_path,
    bus_files_dir,
    resolve_inside_workspace,
    team_shared_dir,
)


def _base(base: Optional[str]) -> str:
    if base is None:
        from narranexus.platform.settings import settings

        return settings.base_working_path
    return base


def _today_str() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def _sniff_mime(name: str) -> str:
    """Best-effort MIME from a filename. The agent's built-in Read re-validates
    content at read time, so a coarse extension-based guess is sufficient here."""
    mime, _ = mimetypes.guess_type(name)
    return mime or "application/octet-stream"


def _split(path: "Path | str", base: Optional[str]) -> tuple[str, str]:
    """(realpath(base), base-relative POSIX path) for a LEXICAL path.

    Everything under a workspace or the per-user shared area is tenant-writable
    (the owner's sandbox can plant links there), so every read, write, link and
    stat of such a file goes through ``utils/fs/confined_io`` — each component below
    the base O_NOFOLLOW, operations relative to dir fds, never a path string and
    never a resolve()d tenant tail. ConfinedPathError (a ValueError) when the
    path is not strictly below the base.
    """
    return confined_io.split_under_base(str(path), _base(base))


def _is_regular(path: "Path | str", base: Optional[str]) -> bool:
    try:
        root, rel = _split(path, base)
        return stat.S_ISREG(confined_io.lstat(root, rel).st_mode)
    except (OSError, ValueError):
        return False


def _link_or_copy(src: Path, dst: Path, base: Optional[str] = None) -> None:
    """Hard-link ``src`` → ``dst``; fall back to a byte copy across devices.

    Hard-linking keeps large files from doubling on disk when an agent forwards
    or shares a file. Done fd-relative (``confined_io.link_or_copy``): the source
    is walked O_NOFOLLOW, so a directory the sender swapped for a link can no
    longer make the platform link or copy another tenant's file into the
    sender's shared area.
    """
    sroot, srel = _split(src, base)
    droot, drel = _split(dst, base)
    confined_io.link_or_copy(sroot, srel, droot, drel)


def _resolve_ref_to_source(ref: str, sender_agent_id: str, owner_user_id: str, base: str) -> Optional[Path]:
    """Resolve one send-time handle to an absolute source file, or None.

    - ``att_...`` → an attachment in the sender's ``user_upload_files`` store,
      falling back to the per-user SHARED bus area when the sender's own
      store misses. Both stores mint ids with the same ``att_`` format via
      ``generate_file_id``, so a team-chat attachment a human uploaded
      directly (or one another agent already staged) is indistinguishable
      from the sender's own upload by shape alone — only the fallback tells
      them apart. Without it, `team_share_file` re-sharing a team-chat
      attachment into another team silently resolved to nothing (#122).
      Boundary of the fallback: `resolve_shared_file_by_id` is scoped to
      `owner_user_id`'s own root and the id has already passed the `att_`
      format check, so it can reach any file in THIS owner's shared area
      (any of the owner's rooms, teams included) and never another owner's.
      That is the same user-wide grant `turn_accessible_roots` already
      gives every turn on the whole `bus_files` tree; the fallback removes
      a Read-then-attach detour, not a partition.
    - anything else → a path relative to the sender's workspace, validated to
      stay inside that workspace (rejects ``../`` escapes, absolute paths
      elsewhere, and a symlinked workspace dir — see resolve_inside_workspace).
    """
    ref = (ref or "").strip()
    if not ref:
        return None

    if is_valid_file_id(ref):
        own_upload = resolve_attachment_path(sender_agent_id, owner_user_id, ref)
        if own_upload is not None:
            return own_upload
        return resolve_shared_file_by_id(owner_user_id, ref, base)

    # Lexical workspace on a realpath-ed base: the sender can swap its own
    # workspace dir for a link to another tenant's, and resolving the root
    # would make that tree "the workspace" (2026-10-28).
    workspace = agent_workspace_path(sender_agent_id, owner_user_id, base=os.path.realpath(base))
    try:
        resolved = resolve_inside_workspace(workspace, ref)
        # The RESOLVED candidate only proves the ref stays inside the workspace;
        # what the caller gets back is the LEXICAL path (workspace + the proven
        # tail), which every later open walks O_NOFOLLOW — a directory swapped
        # for a link after this check is refused, never followed.
        candidate = workspace / resolved.relative_to(Path(os.path.abspath(workspace)))
    except ValueError as e:
        logger.warning(f"[bus attachment] rejected workspace-escaping ref: {ref!r} ({e})")
        return None
    if not _is_regular(candidate, base):
        logger.warning(f"[bus attachment] ref not found: {ref!r}")
        return None
    return candidate


def _base_root(base: str) -> Path:
    return Path(base)


def _bus_att_dict(target: Path, base: str, *, original_name: str, mime: str) -> dict:
    """Build the bus-attachment dict for a file already written to ``target``.

    ``target`` is built on the REALPATH of the base (``_split``), never on a
    resolve()d tenant tail, so a symlinked base (e.g. macOS ``/var`` →
    ``/private/var``) cannot yield a ``../``-laden path that breaks the marker
    and the shared-file resolver.
    """
    root, rel = _split(target, base)
    return {
        "file_id": target.stem,
        "original_name": original_name or target.name,
        "mime_type": mime,
        "size_bytes": confined_io.lstat(root, rel).st_size,
        "category": derive_category_from_mime(mime).value,
        "rel_path": Path(rel).as_posix(),
    }


def _new_target(dest_dir: Path, filename: str, base: Optional[str] = None) -> Path:
    """Fresh ``{file_id}{suffix}`` path inside ``dest_dir`` (dir created through
    the link-free walk). The suffix is ``on_disk_suffix(filename)``, so no
    character of an author's file name reaches the disk path unsanitised
    (defence in depth: the Read-tool marker prints the path as an exact JSON
    literal). The returned path is LEXICAL on the realpath of the base — never an
    ``ensure_within_directory`` (resolve()) result."""
    root, rel = _split(dest_dir, base)
    confined_io.make_dirs(root, rel)
    file_id = generate_file_id()
    suffix = on_disk_suffix(filename)
    on_disk_name = f"{file_id}{suffix}" if suffix else file_id
    on_disk_name = sanitize_filename(on_disk_name, label="bus attachment")
    return Path(root) / rel / on_disk_name


async def _stage_into(src: Path, dest_dir: Path, base: str, *, original_name: str) -> dict:
    """Hard-link/copy ``src`` into ``dest_dir`` under a fresh file_id, returning
    the bus-attachment dict (metadata + base-relative staged path).

    The disk write runs in a thread: the hard-link path is instant, but the
    cross-device ``shutil.copy2`` fallback can move tens of MB and must not
    block the event loop the caller (route / MCP tool) runs on.
    """
    target = _new_target(dest_dir, src.name, base)
    await asyncio.to_thread(_link_or_copy, src, target, base)
    return _bus_att_dict(target, base, original_name=original_name, mime=_sniff_mime(original_name or src.name))


async def store_bytes_into_bus(
    *,
    user_id: str,
    raw_bytes: bytes,
    original_name: str,
    mime_type: str,
    base: Optional[str] = None,
) -> dict:
    """Write user-uploaded bytes into the per-user shared bus area and return
    the bus-attachment dict — the upload counterpart to ``resolve_and_stage_refs``.

    Used by the team-chat upload endpoint: a human attaches a file to a team
    message, so there is no source workspace file to reference — we persist the
    raw bytes directly under ``{base}/{user_id}/_shared/bus_files/{date}/``.
    Callers pass an authenticated ``user_id`` and a server-sniffed ``mime_type``.
    The write runs in a thread — uploads go up to ``MAX_UPLOAD_BYTES`` (50 MB
    default) and must not block the route's event loop.
    """
    base = _base(base)
    dest_dir = bus_files_dir(user_id, base) / _today_str()
    target = _new_target(dest_dir, original_name or "", base)

    def _write() -> None:
        root, rel = _split(target, base)
        with confined_io.open_new(root, rel) as f:
            f.write(raw_bytes)

    await asyncio.to_thread(_write)
    return _bus_att_dict(target, base, original_name=original_name or target.name, mime=mime_type)


def _meta_sidecar_path(staged: Path) -> Path:
    """Sidecar path for a staged file's server-authoritative attachment dict.

    ``{file_id}_meta.json`` next to the staged file. The underscore (not a
    dot) keeps it invisible to ``resolve_shared_file_by_id``'s
    ``{file_id}.*`` glob, so the sidecar can never shadow the original.
    """
    return staged.parent / f"{staged.stem}_meta.json"


def store_bus_attachment_meta(
    user_id: str, attachment: dict, base: Optional[str] = None
) -> None:
    """Persist the server-built attachment dict next to its staged file.

    Written by the upload endpoint AFTER it finishes building the dict
    (including a Whisper ``transcript``). At send time the client echoes
    attachment dicts back over the wire; the server re-reads this sidecar
    instead of trusting any echoed field — see ``load_bus_attachment_meta``.
    Best-effort: a sidecar write failure must not fail the upload.
    """
    staged = resolve_shared_file_for_user(user_id, attachment.get("rel_path") or "", base)
    if staged is None:
        return
    try:
        root, rel = _split(_meta_sidecar_path(staged), base)
        confined_io.write_atomic(root, rel, json.dumps(attachment, ensure_ascii=False).encode("utf-8"))
    except (OSError, ValueError) as e:
        logger.warning(f"[bus attachment] failed to write meta sidecar: {e}")


def load_bus_attachment_meta(
    user_id: str, rel_path: str, base: Optional[str] = None
) -> Optional[dict]:
    """Load the server-authoritative attachment dict for a staged file.

    Returns None when the rel_path fails the shared-area gate or no sidecar
    exists (e.g. agent-staged files, which never pass through the upload
    endpoint). Callers fall back to rebuilding minimal metadata from disk.
    """
    staged = resolve_shared_file_for_user(user_id, rel_path, base)
    if staged is None:
        return None
    sidecar = _meta_sidecar_path(staged)
    try:
        root, rel = _split(sidecar, base)
        with confined_io.open_read(root, rel) as f:
            loaded = json.loads(f.read().decode("utf-8"))
    except (OSError, ValueError):  # missing, a link on the way, or corrupt
        return None
    return loaded if isinstance(loaded, dict) else None


async def resolve_and_stage_refs(
    *,
    sender_agent_id: str,
    owner_user_id: str,
    refs: List[str],
    base: Optional[str] = None,
) -> List[dict]:
    """Resolve send-time file handles and stage them into the shared bus area.

    Args:
        sender_agent_id: The agent attaching the files (authenticated context).
        owner_user_id: The owning user (both agents' workspace root).
        refs: Handles — ``att_...`` file_ids and/or workspace-relative paths.
        base: Override base_working_path (tests); defaults to settings.

    Returns:
        List of bus-attachment dicts. Unresolvable handles are logged and
        skipped — a bad attachment must never abort the message send.
    """
    base = _base(base)
    dest_dir = bus_files_dir(owner_user_id, base) / _today_str()
    out: List[dict] = []
    for ref in refs:
        src = _resolve_ref_to_source(ref, sender_agent_id, owner_user_id, base)
        if src is None:
            continue
        try:
            out.append(await _stage_into(src, dest_dir, base, original_name=src.name))
        except Exception as e:  # noqa: BLE001 — one bad file must not drop the message
            logger.warning(f"[bus attachment] failed to stage {ref!r}: {e}")
    return out


def _content_hash(path: Path, base: Optional[str] = None) -> str:
    """sha256 of a file's bytes, streamed through the link-free walk.

    sha256 rather than md5: the same cost in practice, and it is the hash a
    content-addressed store would reuse later, so the index does not have to
    be re-computed to get there.
    """
    h = hashlib.sha256()
    root, rel = _split(path, base)
    with confined_io.open_read(root, rel) as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


class _DedupProbe(NamedTuple):
    """Outcome of the duplicate lookup.

    A named pair rather than the previous dict-or-str-or-None: the caller had
    to `isinstance` its way back to the meaning, and the digest being a bare
    `str` in one branch made it easy to read as "no match" by accident.
    """

    row: Optional[dict]
    #: The source's digest when it was computed — reused so the insert path
    #: never hashes the same file twice.
    digest: Optional[str]


async def _find_duplicate(
    db, team_id: str, name: str, size: int, src: Path, base: Optional[str] = None
) -> _DedupProbe:
    """The existing row for this exact file, plus the digest if one was taken.

    Two-stage on purpose. Hashing reads the whole file, so it runs only after
    a cheap indexed lookup on (team, name, size) finds a candidate — a
    genuinely new file is never hashed. Same name with a different hash is NOT
    a duplicate: those are two different files that happen to share a name,
    and folding one onto the other would silently destroy a share.
    """
    candidates = await TeamFileRepository(db).find_by_name_and_size(team_id, name, size)
    if not candidates:
        return _DedupProbe(None, None)
    digest = await asyncio.to_thread(_content_hash, src, base)
    for row in candidates:
        if row.get("content_hash") == digest:
            return _DedupProbe(row, digest)
    return _DedupProbe(None, digest)


async def stage_path_into_team(
    *,
    sender_agent_id: str,
    owner_user_id: str,
    team_id: str,
    ref: str,
    base: Optional[str] = None,
    db=None,
) -> Optional[dict]:
    """Stage one workspace file (or att_ file_id) into a team's shared scratch
    dir and index it. Returns the staged dict with an added absolute ``path``,
    or None if the handle does not resolve. Membership/ownership are validated
    by the caller.

    The index row is what makes the folder enumerable at all: files land under
    a generated file_id, so without a row the original name exists nowhere and
    the only way to find anything is an agent reciting a path in chat.

    Re-sharing a byte-identical file reuses the existing row and skips the
    copy. Indexing failures are logged, never raised: the file is already on
    disk and reachable by path at that point, so refusing the share to protect
    the bookkeeping would cost the user the actual outcome.
    """
    base = _base(base)
    src = _resolve_ref_to_source(ref, sender_agent_id, owner_user_id, base)
    if src is None:
        return None

    if db is None:
        from narranexus.platform.utils.db.db_factory import get_db_client
        db = await get_db_client()

    name = src.name
    sroot, srel = _split(src, base)
    size = confined_io.lstat(sroot, srel).st_size
    digest = None
    try:
        probe = await _find_duplicate(db, team_id, name, size, src, base)
        digest = probe.digest
        if probe.row is not None:
            reused = Path(os.path.realpath(base)) / probe.row["rel_path"]
            # Only reuse a row whose file is still there. `_wipe_team_data`
            # removes the folder without touching rows it does not own, and a
            # row pointing at nothing would hand the agent a dead path while
            # reporting success.
            if _is_regular(reused, base):
                return {
                    "file_id": probe.row["file_id"],
                    "original_name": probe.row["original_name"],
                    "mime_type": probe.row.get("mime_type"),
                    "size_bytes": int(probe.row.get("size_bytes") or 0),
                    "category": probe.row.get("category"),
                    "rel_path": probe.row["rel_path"],
                    "path": str(reused),
                }
            logger.info(
                f"[team files] indexed row for {name!r} lost its file; re-staging"
            )
    except Exception as e:  # noqa: BLE001 — dedup is an optimisation, not a gate
        logger.warning(f"[team files] dedup lookup failed for {name!r}: {e}")

    dest_dir = team_shared_dir(owner_user_id, team_id, base)
    staged = await _stage_into(src, dest_dir, base, original_name=name)
    staged["path"] = str(Path(os.path.realpath(base)) / staged["rel_path"])

    if digest is None:
        digest = await asyncio.to_thread(_content_hash, src, base)

    try:
        await TeamFileRepository(db).add({
            "file_id": staged["file_id"],
            "team_id": team_id,
            "owner_user_id": owner_user_id,
            "shared_by_agent_id": sender_agent_id,
            "original_name": staged["original_name"],
            "rel_path": staged["rel_path"],
            "mime_type": staged.get("mime_type"),
            "category": staged.get("category"),
            "size_bytes": staged.get("size_bytes") or 0,
            "content_hash": digest,
        })
    except Exception as e:  # noqa: BLE001 — see docstring
        # Losing a race is the expected shape of this failure, not a bug: the
        # probe above is check-then-act with no lock, so two concurrent shares
        # of identical bytes both miss it and the second one hits the UNIQUE
        # index. Swallowing that would leave the file on disk with NO row —
        # invisible in the panel and to team_list_files, exactly what the
        # index exists to prevent. Adopt the winner's row instead; the caller
        # gets the same answer a sequential duplicate share would have given.
        winner = None
        try:
            winner = await TeamFileRepository(db).find_exact(
                team_id, staged["original_name"], digest
            )
        except Exception:  # noqa: BLE001 — recovery must not mask the original
            winner = None
        if winner:
            logger.debug(f"[team files] adopted concurrent row for {name!r}")
            return {
                "file_id": winner["file_id"],
                "original_name": winner["original_name"],
                "mime_type": winner.get("mime_type"),
                "size_bytes": int(winner.get("size_bytes") or 0),
                "category": winner.get("category"),
                "rel_path": winner["rel_path"],
                "path": str(Path(os.path.realpath(base)) / winner["rel_path"]),
            }
        logger.warning(f"[team files] index row not written for {name!r}: {e}")
    return staged


def build_bus_markers(
    attachments: Optional[List[dict]],
    *,
    from_agent: str = "",
    base: Optional[str] = None,
) -> str:
    """Render bus-attachment dicts as newline-joined Read-tool markers.

    Rendered by ``file_marker``, the renderer ``Attachment.synthesize_marker``
    uses too, so recipient behaviour is uniform with user-uploaded files — the agent sees an absolute path and a
    ``use Read tool`` instruction. The absolute path is rebuilt from
    ``base_working_path`` + the stored base-relative ``rel_path`` (drift-tolerant,
    like ``instance_artifacts.file_path``). Empty/malformed input → "".

    One marker is one line, and no field of it can be forged: ``file_marker``
    emits every value (name, path, mime, kind, sender, transcript) as a JSON
    string literal, so nothing can start a row of the prompt block the marker
    sits in (a scrollback `User: ...` line) or close the marker early.
    ``from_agent`` is the RAW sender (a handle or a display name), never a
    pre-encoded label: ``file_marker`` encodes it.
    """
    if not attachments:
        return ""
    root = _base_root(_base(base))
    real_root = os.path.realpath(str(root))
    lines: List[str] = []
    for att in attachments:
        if not isinstance(att, dict):
            continue
        rel = att.get("rel_path")
        if not rel:
            continue
        path = os.path.normpath(os.path.join(real_root, rel))
        # rel_path is written by _bus_att_dict today; a row from any other
        # writer must still not put a path outside the base into the prompt.
        if not path.startswith(real_root + os.sep):
            continue
        transcript = att.get("transcript")
        lines.append(
            file_marker(
                "Shared file",
                name=att.get("original_name"),
                path=path,
                mime=att.get("mime_type") or "application/octet-stream",
                kind=att.get("category") or "file",
                sender=from_agent,
                # A voice memo: the spoken text is surfaced inline so the
                # recipient agent reads it directly (it cannot listen).
                transcript=transcript if isinstance(transcript, str) else None,
            )
        )
    return "\n".join(lines)


def resolve_shared_file_by_id(
    user_id: str, file_id: str, base: Optional[str] = None
) -> Optional[Path]:
    """Resolve a bus-attachment ``file_id`` to its file in the shared area.

    The shared bus area keeps no ``_index.json`` (the DB row is the index), so
    resolution-by-id globs ``{base}/{user_id}/_shared/bus_files/*/{file_id}.*``.
    Prefers the ORIGINAL upload over a ``.mp3`` transcode sibling. Used by the
    public transcription endpoint as a fallback when the agent-scoped resolver
    misses (team voice memos live here, not in an agent's user_upload_files).
    Scoped to the caller's own user root; validates the file_id format.
    """
    if not is_valid_file_id(file_id):
        return None
    root = bus_files_dir(user_id, _base(base))
    # Listed through the link-free walk (date dirs, then names): a `_shared` /
    # `bus_files` / date dir swapped for a link lists as absent, and the result
    # is a LEXICAL path whose every later open is O_NOFOLLOW again.
    try:
        broot, brel = _split(root, base)
        date_dirs = [i for i in confined_io.list_dir(broot, brel) if i.is_dir]
    except (OSError, ValueError):
        return None
    matches: List[Path] = []
    for day in sorted(date_dirs, key=lambda i: i.name):
        try:
            names = confined_io.list_dir(broot, f"{brel}/{day.name}")
        except (OSError, ValueError):
            continue
        for entry in sorted(names, key=lambda i: i.name):
            if entry.is_file and (entry.name == file_id or entry.name.startswith(f"{file_id}.")):
                matches.append(Path(broot) / brel / day.name / entry.name)
    original = next((p for p in matches if p.suffix.lower() != ".mp3"), None)
    if original is not None:
        return original
    return matches[0] if matches else None


def resolve_shared_file_for_user(
    user_id: str, rel_path: str, base: Optional[str] = None
) -> Optional[Path]:
    """Resolve a bus-attachment ``rel_path`` to an absolute file for serving.

    Used by the frontend download endpoints. The caller passes the
    AUTHENTICATED ``user_id``; the ``rel_path`` comes from a message's stored
    attachment dict (and is therefore attacker-influenceable over the wire), so
    we gate it hard:

      * the path's first segment MUST equal ``user_id`` (a user may only fetch
        files under their own per-user root — which is also the only place bus
        attachments for their agents can live);
      * the resolved absolute path MUST stay within ``{base}/{user_id}`` (blocks
        ``../`` traversal);
      * the file must exist.

    Returns the absolute path, or None on any check failure (caller → 404).
    """
    base = _base(base)
    rel = (rel_path or "").replace("\\", "/").lstrip("/")
    parts = rel.split("/")
    if not parts or parts[0] != user_id or any(p in ("", ".", "..") for p in parts):
        return None
    # Lexical candidate below the realpath of the base: only real directories
    # are walked (O_NOFOLLOW), so there is nothing to resolve() — and no link a
    # tenant planted under its own root can redirect the lookup elsewhere.
    candidate = Path(os.path.realpath(base)) / rel
    if not _is_regular(candidate, base):
        return None
    return candidate
