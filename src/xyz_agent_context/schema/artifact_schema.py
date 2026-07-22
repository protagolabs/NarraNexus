"""
@file_name: artifact_schema.py
@author: Bin Liang
@date: 2026-05-08
@description: Pydantic models for agent-emitted Artifacts (charts, reports, html apps, csv, images, pdf)

Pointer model (2026-05-14): an Artifact is a pointer to an entry file the agent
wrote inside its own workspace. Content is never copied into a managed store —
`file_path` points at the live workspace file, and the file's directory is the
artifact root (served wholesale so multi-file HTML apps can reference siblings).

Models:
- ArtifactKind: literal whitelist of allowed mime-like kinds
- Artifact: metadata row; session_id NULL ⇔ pinned (agent-scoped)
- CreateArtifactToolResult: what register_artifact returns to the LLM
- HealCandidate / HealResult: outcome of the broken-pointer recovery flow
  (ArtifactService.heal); doubles as the heal endpoint's response model
"""

from __future__ import annotations

from datetime import datetime
from typing import List, Literal, Optional

from pydantic import BaseModel, Field


ArtifactKind = Literal[
    "text/html",
    "application/vnd.echarts+json",
    "text/csv",
    "text/markdown",
    "image/png",
    "image/jpeg",
    "application/pdf",
    # Office document (.pptx/.docx/.xlsx) — renders as a LIVE officecli-watch
    # preview (auto-refreshes as the agent edits), not a static file.
    "application/vnd.officecli-live",
]


class Artifact(BaseModel):
    artifact_id: str  # "art_" + 8 random chars
    agent_id: str
    user_id: str
    session_id: Optional[str] = None
    original_session_id: Optional[str] = None
    title: str = Field(..., max_length=200)
    kind: ArtifactKind
    description: Optional[str] = Field(default=None, max_length=2000)
    pinned: bool = False
    file_path: str  # entry file, relative to settings.base_working_path
    size_bytes: int = 0  # recursive size of the artifact root directory
    created_at: datetime
    updated_at: datetime


class CreateArtifactToolResult(BaseModel):
    artifact_id: str
    url: str
    created_at: datetime


class HealCandidate(BaseModel):
    """One workspace file the heal scan surfaced as a plausible re-register
    target for a broken pointer (extension matches the artifact kind)."""

    workspace_path: str  # path relative to the agent workspace, e.g. "briefings/2026-05-19.html"
    size_bytes: int
    mtime: float  # unix epoch seconds


class HealResult(BaseModel):
    """Outcome of a heal attempt (see ArtifactService.heal for the strategy).

    recovered=True → `artifact` holds the (possibly re-registered) row.
    recovered=False → `candidates` holds 0..N options for the user to pick
    from; `message` explains the situation either way.
    """

    recovered: bool
    artifact: Optional[Artifact] = None  # populated when recovered=True
    candidates: List[HealCandidate] = Field(default_factory=list)
    message: str
