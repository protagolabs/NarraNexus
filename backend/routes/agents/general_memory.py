"""
@file_name: general_memory.py
@author:
@date: 2026-08-10
@description: General-memory endpoints for the MCP data-access seam (PR-2).

Backend counterparts of the GeneralMemoryModule MCP tools (remember /
grep_memory / memory_retain) so the Http path of AgentDataStore can serve
them without db credentials in the mcp container. Implemented in this PR.
"""
from __future__ import annotations

from fastapi import APIRouter

router = APIRouter()
