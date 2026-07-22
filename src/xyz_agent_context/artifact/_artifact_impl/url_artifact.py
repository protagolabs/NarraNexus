"""
@file_name: url_artifact.py
@author: Bin Liang
@date: 2026-07-22
@description: Create / update URL-tab artifacts (application/x-url).

A URL tab is an ordinary pointer artifact whose entry file is a small JSON
doc (UrlArtifactDoc) at `tabs/<slug>/page.url.json` in the agent workspace.
This module owns the doc I/O + orchestration; registration itself goes through
the shared `registration.register_artifact` so URL tabs get heal / delete /
bundle / raw-serving for free.

Each URL tab lives in its OWN subdirectory so the raw route's artifact-root
isolation means one tab can never read another tab's json.

No DB column for the URL — the JSON doc is the source of truth (pointer model
preserved). Updating the embed decision = rewriting the doc.
"""

from __future__ import annotations

import json
import os
import secrets
from typing import Optional

from loguru import logger

from xyz_agent_context.artifact._artifact_impl import registration
from xyz_agent_context.artifact._artifact_impl.embed_probe import probe_url
from xyz_agent_context.artifact._artifact_impl.errors import (
    ArtifactError,
    ArtifactNotFound,
)
from xyz_agent_context.repository.artifact_repository import ArtifactRepository
from xyz_agent_context.schema.artifact_schema import (
    URL_ARTIFACT_KIND,
    Artifact,
    CreateArtifactToolResult,
    EmbedMode,
    UrlArtifactDoc,
)
from xyz_agent_context.settings import settings
from xyz_agent_context.utils.url_safety import UnsafeUrlError, assert_public_http_url

_URL_TABS_DIR = "tabs"
_DOC_FILENAME = "page.url.json"


def _our_scheme() -> str:
    """The scheme WE are served on, for the mixed-content check. Cloud is
    https; local dev is http. Derived from settings.public_base_url when set,
    else assumed http (local run.sh)."""
    base = (settings.public_base_url or "").strip().lower()
    if base.startswith("https://"):
        return "https"
    return "http"


def _doc_abs_path(agent_id: str, user_id: str, rel_entry: str) -> str:
    """Absolute path of a URL-tab doc. `rel_entry` is relative to the AGENT
    WORKSPACE root (the same base registration._resolve_entry joins against),
    NOT to base_working_path — the nested layout lives under the workspace."""
    workspace = registration.workspace_root(agent_id, user_id)
    return os.path.join(workspace, rel_entry)


def _read_doc(abs_path: str) -> UrlArtifactDoc:
    with open(abs_path, "r", encoding="utf-8") as f:
        return UrlArtifactDoc.model_validate_json(f.read())


def _write_doc(abs_path: str, doc: UrlArtifactDoc) -> None:
    os.makedirs(os.path.dirname(abs_path), exist_ok=True)
    with open(abs_path, "w", encoding="utf-8") as f:
        f.write(doc.model_dump_json(indent=2))


async def open_url(
    *,
    repo: ArtifactRepository,
    agent_id: str,
    user_id: str,
    session_id: Optional[str],
    url: str,
    title: Optional[str] = None,
) -> CreateArtifactToolResult:
    """Create a URL-tab artifact.

    Steps:
    1. Hard-reject an obviously-internal initial URL (SSRF gate). This raises
       ArtifactError(400) — tab creation fails loudly.
    2. Probe the URL server-side for its embed verdict (never crashes; a
       failed probe degrades to an optimistic iframe verdict).
    3. Write the UrlArtifactDoc into a dedicated `tabs/<slug>/` subdir.
    4. Register it through the shared pointer path.

    Raises:
        ArtifactError: initial URL is not a safe public http(s) target.
    """
    try:
        await assert_public_http_url(url)
    except UnsafeUrlError as e:
        raise ArtifactError(f"refusing to open a non-public URL: {e}") from e

    verdict = await probe_url(url, our_scheme=_our_scheme())

    slug = secrets.token_hex(4)
    rel_entry = f"{_URL_TABS_DIR}/{slug}/{_DOC_FILENAME}"
    abs_entry = _doc_abs_path(agent_id, user_id, rel_entry)
    resolved_title = (title or url)[:200]
    _write_doc(abs_entry, UrlArtifactDoc(url=url, title=resolved_title, embed=verdict))

    result = await registration.register_artifact(
        repo=repo,
        agent_id=agent_id,
        user_id=user_id,
        session_id=session_id,
        kind=URL_ARTIFACT_KIND,  # type: ignore[arg-type]
        entry_path=rel_entry,
        title=resolved_title,
        description=url,  # human-readable pointer target for listings
        target_artifact_id=None,
    )
    logger.debug("Opened URL artifact {} -> {}", result.artifact_id, url)
    return result


async def set_embed_mode(
    *,
    repo: ArtifactRepository,
    agent_id: str,
    artifact_id: str,
    mode: Optional[EmbedMode],
) -> Artifact:
    """Set (or clear, with mode=None) the user's manual embed override on a
    URL tab, by rewriting its JSON doc. Returns the artifact row.

    Raises:
        ArtifactNotFound: artifact missing / not this agent's / not a URL tab.
    """
    art = await repo.get_by_id(artifact_id)
    if art is None or art.agent_id != agent_id or art.kind != URL_ARTIFACT_KIND:
        raise ArtifactNotFound("URL artifact not found")

    from xyz_agent_context.utils.workspace_paths import resolve_workspace_relative_file

    base = os.path.realpath(settings.base_working_path)
    abs_entry = os.path.realpath(
        str(resolve_workspace_relative_file(art.file_path, art.agent_id, art.user_id, base))
    )
    doc = _read_doc(abs_entry)
    if doc.embed is None:
        # No probe verdict on disk (shouldn't happen for a real URL tab, but be
        # defensive): synthesize an iframe default to hang the override on.
        from xyz_agent_context.schema.artifact_schema import EmbedVerdict

        doc.embed = EmbedVerdict(recommended="iframe", reason="no-blocking-headers")
    doc.embed.user_override = mode
    _write_doc(abs_entry, doc)

    # Bump updated_at so the frontend's cache-bust / refetch sees a change.
    await repo.update_title(artifact_id, art.title)
    refreshed = await repo.get_by_id(artifact_id)
    assert refreshed is not None
    return refreshed
