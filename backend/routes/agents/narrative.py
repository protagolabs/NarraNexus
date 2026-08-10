"""
@file_name: narrative.py
@author:
@date: 2026-08-10
@description: Narrative endpoints for the MCP data-access seam (PR-2).

Backend counterparts of the BasicInfoModule narrative MCP tools
(view_narrative / switch_narrative / create_narrative). Implemented in this PR.
"""
from __future__ import annotations

from fastapi import APIRouter

router = APIRouter()
