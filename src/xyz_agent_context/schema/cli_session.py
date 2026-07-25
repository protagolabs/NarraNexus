"""
@file_name: cli_session.py
@author:
@date: 2026-07-24
@description: Data model for resumable coding-agent CLI session handles.

One row per (agent_id, platform_session_id, framework) in the
``agent_cli_sessions`` table: the CLI session id the platform may resume
(``--resume <cli_session_id>``) instead of cold-starting with the full
conversation history in the system prompt.

The handle is scoped by three validity anchors captured at write time:
- ``narrative_id`` — narrative switch means the topic domain changed and
  history composition is re-decided by step_1; a stored handle bound to a
  different narrative must NOT be resumed (rule, not a defect).
- ``config_fingerprint`` — ``ClaudeConfig.resume_fingerprint()``; a changed
  provider / model / auth kind / config dir means the session file may not
  exist or may not be safe to resume.
- ``working_path`` — the CLI archives session jsonl files under a slug of
  its launch cwd; a different cwd means the file is not where ``--resume``
  will look.

Any mismatch (or absence) simply causes a cold start — resume is an
optimization, never a correctness dependency.
"""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field

from xyz_agent_context.utils.timezone import utc_now


class AgentCliSession(BaseModel):
    """Resumable CLI session handle for one platform conversation."""

    # Auto-increment surrogate key; None until the row is inserted.
    id: Optional[int] = None

    # ===== Unique key (idx_cli_sessions_key) =====
    agent_id: str
    # ConversationSession.session_id (sess_xxxxxxxx) — the platform-side
    # conversation this CLI session continues.
    platform_session_id: str
    # Coding-agent framework that produced the handle ("claude_code" in v1).
    framework: str

    # ===== Handle payload =====
    # ResultMessage.session_id from the CLI — the value passed to --resume.
    cli_session_id: str
    # sha256(auth_type|base_url|config_dir|model)[:16] — mismatch => cold start.
    config_fingerprint: str
    # Launch cwd of the CLI (sessions are archived under its slug).
    working_path: str
    # Narrative bound at capture time; narrative switch => cold start.
    narrative_id: Optional[str] = None

    # ===== Timestamps =====
    last_used_at: datetime = Field(default_factory=utc_now)
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None
