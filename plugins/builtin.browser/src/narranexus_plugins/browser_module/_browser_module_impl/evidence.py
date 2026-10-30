"""
@file_name: evidence.py
@author:
@date: 2026-09-22
@description: Register browser screenshots through the existing artifact service.
"""
from __future__ import annotations

import asyncio
import base64
import secrets
from pathlib import Path
from typing import Any

from narranexus.platform.artifact import ArtifactService, MAX_ARTIFACT_BYTES
from narranexus.platform.module_system.data_access import resolve_agent_workspace_cwd
from narranexus.platform.settings import settings
from narranexus.platform.utils.fs import confined_io
from narranexus.platform.utils.db.db_factory import get_db_client


async def save_evidence(*, agent_id: str, identity: Any, capture: dict, title: str) -> dict:
    """Write one fresh image, then register its pointer and turn attribution."""
    mime_type = capture.get("mime_type", "image/png")
    if mime_type not in {"image/png", "image/jpeg"}:
        raise ValueError("Browser returned an unsupported screenshot type")
    data = capture.get("data")
    if not isinstance(data, str) or len(data) > ((MAX_ARTIFACT_BYTES + 2) // 3) * 4:
        raise ValueError("Browser screenshot is missing or exceeds the artifact size limit")
    content = base64.b64decode(data, validate=True)
    signature = b"\x89PNG\r\n\x1a\n" if mime_type == "image/png" else b"\xff\xd8\xff"
    if not content.startswith(signature) or len(content) > MAX_ARTIFACT_BYTES:
        raise ValueError("Browser returned invalid screenshot bytes")
    workspace = await resolve_agent_workspace_cwd(agent_id, log_tag="browser-evidence")
    if workspace is None:
        raise ValueError("The agent workspace is unavailable; evidence was not saved")

    # The workspace is the agent's own (tenant-writable) mount: the evidence dir
    # and image are created THROUGH the link-free walk below the workspace base
    # (random dir name, O_EXCL file, every component O_NOFOLLOW). mkdtemp /
    # write_bytes by path followed a swapped directory into another tenant's tree.
    ws_root, ws_rel = confined_io.split_under_base(str(workspace), settings.base_working_path)
    dir_rel = f"{ws_rel}/browser-evidence-{secrets.token_hex(6)}"

    def write_image() -> Path:
        name = "capture.png" if mime_type == "image/png" else "capture.jpg"
        confined_io.make_dirs(ws_root, dir_rel)
        with confined_io.open_new(ws_root, f"{dir_rel}/{name}", mode=0o600) as f:
            f.write(content)
        return Path(ws_root) / dir_rel / name

    path = await asyncio.to_thread(write_image)
    try:
        artifact = await ArtifactService(await get_db_client()).register(
            agent_id=agent_id,
            user_id=identity.user_id,
            session_id=None,
            kind=mime_type,
            entry_path=str(path),
            title=title,
            description="Browser screenshot evidence",
            event_id=identity.event_id,
        )
    except BaseException:
        # A failed registration must not leave an unregistered sensitive image.
        try:
            confined_io.remove_tree(ws_root, dir_rel)
        except (OSError, ValueError):
            pass
        raise
    return {"outcome": "OK", "artifact": artifact.model_dump(mode="json")}
