"""
@file_name: artifact_runner.py
@author: Bin Liang
@date: 2026-05-08
@description: Filesystem + DB orchestration for create_artifact / upload_artifact_file.

Public functions:
- create_text_artifact(repo, agent_id, user_id, session_id, kind, content, title, description, target_artifact_id)
- upload_binary_artifact(repo, agent_id, user_id, session_id, kind, local_path, title, description, target_artifact_id)

Both return CreateArtifactToolResult and raise structured exceptions for the
MCP wrapper to convert into LLM-readable errors.

File layout: {base_working_path}/{agent_id}_{user_id}/artifacts/{artifact_id}/v{n}.{ext}
"""

from __future__ import annotations

import os
import secrets
import shutil
from datetime import datetime, timezone
from typing import Optional

from loguru import logger

from xyz_agent_context.repository.artifact_repository import ArtifactRepository
from xyz_agent_context.utils.artifact_events import get_artifact_event_bus
from xyz_agent_context.schema.artifact_schema import (
    Artifact,
    ArtifactKind,
    CreateArtifactToolResult,
)
from xyz_agent_context.settings import settings


MAX_TEXT_BYTES = 1 * 1024 * 1024       # 1 MB
MAX_BINARY_BYTES = 10 * 1024 * 1024    # 10 MB
PER_AGENT_QUOTA_BYTES = 500 * 1024 * 1024  # 500 MB

_KIND_TO_EXT: dict[str, str] = {
    "text/html": "html",
    "application/vnd.echarts+json": "json",
    "text/csv": "csv",
    "text/markdown": "md",
    "image/png": "png",
    "image/jpeg": "jpg",
    "application/pdf": "pdf",
}

_TEXT_KINDS = frozenset({"text/html", "application/vnd.echarts+json", "text/csv", "text/markdown"})
_BINARY_KINDS = frozenset({"image/png", "image/jpeg", "application/pdf"})


# ── structured exception hierarchy ────────────────────────────────────────────


class ArtifactError(Exception):
    """Base class for artifact_runner errors. The .code attribute maps to HTTP status."""

    code: int = 400


class ArtifactTooLarge(ArtifactError):
    code = 413


class ArtifactNotFound(ArtifactError):
    code = 404


class ArtifactKindMismatch(ArtifactError):
    code = 400


class ArtifactPathEscape(ArtifactError):
    code = 400


class ArtifactQuotaExceeded(ArtifactError):
    code = 507


# ── path helpers ───────────────────────────────────────────────────────────────


def _new_artifact_id() -> str:
    return "art_" + secrets.token_hex(4)


def _workspace_root(agent_id: str, user_id: str) -> str:
    return os.path.join(settings.base_working_path, f"{agent_id}_{user_id}")


def _artifact_dir(agent_id: str, user_id: str, artifact_id: str) -> str:
    return os.path.join(_workspace_root(agent_id, user_id), "artifacts", artifact_id)


def _relative_to_base(absolute_path: str) -> str:
    """Return path relative to settings.base_working_path."""
    return os.path.relpath(absolute_path, settings.base_working_path)


def _build_url(agent_id: str, artifact_id: str, version: int) -> str:
    return f"/api/agents/{agent_id}/artifacts/{artifact_id}/v{version}/raw"


# ── quota enforcement ──────────────────────────────────────────────────────────


async def _enforce_quota(repo: ArtifactRepository, agent_id: str, incoming_bytes: int) -> None:
    """Raise ArtifactQuotaExceeded if adding incoming_bytes would exceed the per-agent limit."""
    used = await repo.total_bytes_for_agent(agent_id)
    if used + incoming_bytes > PER_AGENT_QUOTA_BYTES:
        raise ArtifactQuotaExceeded(
            f"artifact storage quota exceeded for this agent "
            f"(used={used}, incoming={incoming_bytes}, limit={PER_AGENT_QUOTA_BYTES})"
        )


# ── public API ─────────────────────────────────────────────────────────────────


async def create_text_artifact(
    *,
    repo: ArtifactRepository,
    agent_id: str,
    user_id: str,
    session_id: Optional[str],
    kind: ArtifactKind,
    content: str,
    title: str,
    description: Optional[str],
    target_artifact_id: Optional[str],
) -> CreateArtifactToolResult:
    """
    Persist a text artifact to disk and the database.

    Workflow:
    1. Validate kind is a text kind.
    2. Check content size vs. MAX_TEXT_BYTES.
    3. Enforce per-agent quota.
    4. If target_artifact_id given, validate it exists and kinds match,
       then iterate; otherwise mint a new artifact_id and write version 1.
    5. Write bytes to {base}/{agent}_{user}/artifacts/{id}/v{n}.{ext}.
    6. Insert or iterate DB rows atomically.
    7. Return CreateArtifactToolResult.

    Args:
        repo: ArtifactRepository backed by the active DB client.
        agent_id: Agent that owns the artifact.
        user_id: User that triggered the creation.
        session_id: Session context; None means agent-scoped.
        kind: One of the text ArtifactKind literals.
        content: UTF-8 text payload.
        title: Human-readable title (truncated to 200 chars).
        description: Optional freeform description.
        target_artifact_id: If set, iterate on this existing artifact instead of creating new.

    Returns:
        CreateArtifactToolResult with artifact_id, version, url, created_at.

    Raises:
        ArtifactError: If kind is not a text kind.
        ArtifactTooLarge: If content exceeds MAX_TEXT_BYTES.
        ArtifactQuotaExceeded: If adding bytes would exceed PER_AGENT_QUOTA_BYTES.
        ArtifactNotFound: If target_artifact_id does not exist.
        ArtifactKindMismatch: If target_artifact_id kind differs from requested kind.
    """
    if kind not in _TEXT_KINDS:
        raise ArtifactError(
            f"create_text_artifact does not accept kind={kind!r}; "
            f"use upload_artifact_file for binaries"
        )

    encoded = content.encode("utf-8")
    if len(encoded) > MAX_TEXT_BYTES:
        raise ArtifactTooLarge("content too large (1MB max). For binaries use upload_artifact_file.")

    await _enforce_quota(repo, agent_id, len(encoded))

    is_iteration: bool
    artifact_id: str
    version: int

    if target_artifact_id is not None:
        existing = await repo.get_by_id(target_artifact_id)
        if existing is None:
            raise ArtifactNotFound("artifact not found, omit target_artifact_id to create new")
        if existing.kind != kind:
            raise ArtifactKindMismatch(
                f"cannot iterate {kind} on a {existing.kind} artifact"
            )
        artifact_id = target_artifact_id
        version = existing.latest_version + 1
        is_iteration = True
    else:
        artifact_id = _new_artifact_id()
        version = 1
        is_iteration = False

    folder = _artifact_dir(agent_id, user_id, artifact_id)
    os.makedirs(folder, exist_ok=True)
    ext = _KIND_TO_EXT[kind]
    abs_path = os.path.join(folder, f"v{version}.{ext}")
    with open(abs_path, "wb") as fh:
        fh.write(encoded)
    rel_path = _relative_to_base(abs_path)

    now = datetime.now(timezone.utc)
    if is_iteration:
        await repo.iterate(artifact_id, file_path=rel_path, size_bytes=len(encoded))
        logger.debug("Iterated artifact {} to v{}", artifact_id, version)
    else:
        await repo.create(
            Artifact(
                artifact_id=artifact_id,
                agent_id=agent_id,
                user_id=user_id,
                session_id=session_id,
                title=title[:200],
                kind=kind,
                description=description,
                pinned=False,
                latest_version=1,
                created_at=now,
                updated_at=now,
            ),
            file_path=rel_path,
            size_bytes=len(encoded),
        )
        logger.debug("Created artifact {} v1 kind={}", artifact_id, kind)

    event_type = "artifact.updated" if is_iteration else "artifact.created"
    await get_artifact_event_bus().publish(
        agent_id,
        {
            "type": event_type,
            "artifact_id": artifact_id,
            "version": version,
            "kind": kind,
            "title": title[:200],
            "session_id": session_id,
        },
    )

    return CreateArtifactToolResult(
        artifact_id=artifact_id,
        version=version,
        url=_build_url(agent_id, artifact_id, version),
        created_at=now,
    )


async def upload_binary_artifact(
    *,
    repo: ArtifactRepository,
    agent_id: str,
    user_id: str,
    session_id: Optional[str],
    kind: ArtifactKind,
    local_path: str,
    title: str,
    description: Optional[str],
    target_artifact_id: Optional[str],
) -> CreateArtifactToolResult:
    """
    Copy a binary file from within the agent workspace into the artifact store.

    The source file must already reside inside the agent's workspace directory
    (path-escape check via os.path.realpath). This prevents arbitrary file reads
    from being promoted into user-visible artifacts.

    Workflow:
    1. Validate kind is a binary kind.
    2. Realpath-compare local_path against workspace root (escape check).
    3. Stat file, check size vs. MAX_BINARY_BYTES.
    4. Enforce per-agent quota.
    5. Same create/iterate logic as create_text_artifact.
    6. shutil.copyfile into the artifact dir.
    7. Insert or iterate DB rows.
    8. Return CreateArtifactToolResult.

    Args:
        repo: ArtifactRepository backed by the active DB client.
        agent_id: Agent that owns the artifact.
        user_id: User that triggered the upload.
        session_id: Session context; None means agent-scoped.
        kind: One of the binary ArtifactKind literals.
        local_path: Absolute path to the source file within the agent workspace.
        title: Human-readable title (truncated to 200 chars).
        description: Optional freeform description.
        target_artifact_id: If set, iterate on this existing artifact instead of creating new.

    Returns:
        CreateArtifactToolResult with artifact_id, version, url, created_at.

    Raises:
        ArtifactError: If kind is not a binary kind.
        ArtifactPathEscape: If local_path is outside the agent workspace or does not exist.
        ArtifactTooLarge: If file size exceeds MAX_BINARY_BYTES.
        ArtifactQuotaExceeded: If adding bytes would exceed PER_AGENT_QUOTA_BYTES.
        ArtifactNotFound: If target_artifact_id does not exist.
        ArtifactKindMismatch: If target_artifact_id kind differs from requested kind.
    """
    if kind not in _BINARY_KINDS:
        raise ArtifactError(
            f"upload_artifact_file does not accept kind={kind!r}; "
            f"use create_artifact for text payloads"
        )

    abs_local = os.path.realpath(local_path)
    workspace = os.path.realpath(_workspace_root(agent_id, user_id))
    if not abs_local.startswith(workspace + os.sep):
        raise ArtifactPathEscape("file not found or outside agent workspace")
    if not os.path.isfile(abs_local):
        raise ArtifactPathEscape("file not found or outside agent workspace")

    size = os.path.getsize(abs_local)
    if size > MAX_BINARY_BYTES:
        raise ArtifactTooLarge("file too large (10MB max)")

    await _enforce_quota(repo, agent_id, size)

    is_iteration: bool
    artifact_id: str
    version: int

    if target_artifact_id is not None:
        existing = await repo.get_by_id(target_artifact_id)
        if existing is None:
            raise ArtifactNotFound("artifact not found, omit target_artifact_id to create new")
        if existing.kind != kind:
            raise ArtifactKindMismatch(
                f"cannot iterate {kind} on a {existing.kind} artifact"
            )
        artifact_id = target_artifact_id
        version = existing.latest_version + 1
        is_iteration = True
    else:
        artifact_id = _new_artifact_id()
        version = 1
        is_iteration = False

    folder = _artifact_dir(agent_id, user_id, artifact_id)
    os.makedirs(folder, exist_ok=True)
    ext = _KIND_TO_EXT[kind]
    abs_path = os.path.join(folder, f"v{version}.{ext}")
    shutil.copyfile(abs_local, abs_path)
    rel_path = _relative_to_base(abs_path)

    now = datetime.now(timezone.utc)
    if is_iteration:
        await repo.iterate(artifact_id, file_path=rel_path, size_bytes=size)
        logger.debug("Iterated binary artifact {} to v{}", artifact_id, version)
    else:
        await repo.create(
            Artifact(
                artifact_id=artifact_id,
                agent_id=agent_id,
                user_id=user_id,
                session_id=session_id,
                title=title[:200],
                kind=kind,
                description=description,
                pinned=False,
                latest_version=1,
                created_at=now,
                updated_at=now,
            ),
            file_path=rel_path,
            size_bytes=size,
        )
        logger.debug("Created binary artifact {} v1 kind={}", artifact_id, kind)

    event_type = "artifact.updated" if is_iteration else "artifact.created"
    await get_artifact_event_bus().publish(
        agent_id,
        {
            "type": event_type,
            "artifact_id": artifact_id,
            "version": version,
            "kind": kind,
            "title": title[:200],
            "session_id": session_id,
        },
    )

    return CreateArtifactToolResult(
        artifact_id=artifact_id,
        version=version,
        url=_build_url(agent_id, artifact_id, version),
        created_at=now,
    )
