"""
@file_name: artifact_schema.py
@author: Bin Liang
@date: 2026-05-08
@description: Pydantic models for agent-emitted Artifacts (charts, reports, html apps, csv, images, pdf)

Models:
- ArtifactKind: literal whitelist of allowed mime-like kinds
- ArtifactVersion: one stored content version belonging to an Artifact
- Artifact: metadata row owning N versions; session_id NULL ⇔ pinned (agent-scoped)
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
]


class ArtifactVersion(BaseModel):
    id: int
    artifact_id: str
    version: int
    file_path: str           # relative to settings.base_working_path
    size_bytes: int
    created_at: datetime


class Artifact(BaseModel):
    artifact_id: str         # "art_" + 8 random chars
    agent_id: str
    user_id: str
    session_id: Optional[str] = None
    title: str = Field(..., max_length=200)
    kind: ArtifactKind
    description: Optional[str] = None
    pinned: bool = False
    latest_version: int = 1
    created_at: datetime
    updated_at: datetime


class ArtifactWithVersions(BaseModel):
    artifact: Artifact
    versions: List[ArtifactVersion]


class CreateArtifactToolResult(BaseModel):
    artifact_id: str
    version: int
    url: str
    created_at: datetime
