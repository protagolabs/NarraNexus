"""
@file_name: officecli_client.py
@author: rujing.yan
@date: 2026-07-13
@description: Unified wrapper for all officecli subprocess calls.

Drives the OfficeCLI binary (https://github.com/iOfficeAI/OfficeCLI, npm
``@officecli/officecli``) — a self-contained, no-Office-required engine for
reading/editing .docx/.xlsx/.pptx and rendering them to HTML.

Design mirrors lark_cli_client but is simpler: OfficeCLI needs no per-agent
credential or config hydration — it only reads/writes files. Every agent-scoped
call runs with CWD = the agent's workspace so officecli's default-relative file
paths land inside the agent's Read-tool sandbox (same rationale as lark's
2026-05-28 CWD fix).

Executable resolution reuses the generic ``utils.npm_cli.resolve_npm_cli`` so a
stripped MCP/GUI PATH still finds officecli + its ``env node`` shebang.
"""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
from typing import Any, Optional

from loguru import logger

from xyz_agent_context.utils.npm_cli import resolve_npm_cli


# Office file extension → the ArtifactKind used when registering a preview.
# These are the real OOXML mime types; they match the ArtifactKind literals in
# schema/artifact_schema.py and the _KIND_EXTENSIONS map in agents_artifacts.py.
OFFICE_EXT_TO_KIND: dict[str, str] = {
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    ".pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
}

# Suffix appended (in place of the office extension) for the generated HTML
# preview sibling. The OfficeRenderer front-end derives the same name from the
# artifact's file_path, so KEEP THE TWO IN SYNC (OfficeRenderer.tsx).
PREVIEW_SUFFIX = ".preview.html"


def preview_name_for(office_filename: str) -> str:
    """``slides.pptx`` -> ``slides.preview.html`` (same dir sibling)."""
    stem = Path(office_filename).stem
    return f"{stem}{PREVIEW_SUFFIX}"


class OfficeCLIClient:
    """Stateless async wrapper around the officecli binary."""

    def _workspace_cwd(self, agent_id: str, user_id: str) -> Path:
        """Agent workspace dir used as the subprocess CWD (created if missing).

        Uses the same path the agent's Read tool is sandboxed to, so files
        officecli writes with default ``./...`` paths are readable by the agent
        and servable as artifacts.
        """
        from xyz_agent_context.utils.attachment_storage import get_workspace_path

        ws = get_workspace_path(agent_id, user_id)
        ws.mkdir(parents=True, exist_ok=True)
        return ws

    async def run(
        self,
        agent_id: str,
        user_id: str,
        args: list[str],
        timeout: float = 60.0,
    ) -> dict:
        """Run ``officecli <args>`` in the agent workspace. Returns a result dict.

        On success: ``{"success": True, "data": <parsed-json-or-raw>}``.
        On failure: ``{"success": False, "error": <message>}``.
        """
        cwd = self._workspace_cwd(agent_id, user_id)
        return await self._exec(["officecli", *args], cwd=cwd, timeout=timeout)

    async def render_preview(
        self,
        agent_id: str,
        user_id: str,
        doc_path: str,
        timeout: float = 120.0,
    ) -> dict:
        """Render an office file to a sibling HTML preview via ``officecli view``.

        ``doc_path`` is workspace-relative (or absolute inside the workspace).
        Writes ``<stem>.preview.html`` next to the office file and returns:
            {"success": True, "office_abs", "preview_abs", "office_rel",
             "preview_rel", "kind"}
        or {"success": False, "error": ...}.

        The office file MUST live in a workspace SUBDIRECTORY (not the workspace
        root): the public-raw artifact route only serves sibling files in
        multi-file mode, so a root-level doc's preview would 404. We fail early
        with a clear message rather than register a broken tab.
        """
        cwd = self._workspace_cwd(agent_id, user_id)
        raw = doc_path if os.path.isabs(doc_path) else os.path.join(str(cwd), doc_path)
        office_abs = os.path.realpath(raw)
        workspace = os.path.realpath(str(cwd))

        if not office_abs.startswith(workspace + os.sep):
            return {"success": False, "error": "doc_path is outside the agent workspace."}
        if not os.path.isfile(office_abs):
            return {
                "success": False,
                "error": f"doc_path does not point at an existing file: {doc_path}",
            }

        ext = Path(office_abs).suffix.lower()
        kind = OFFICE_EXT_TO_KIND.get(ext)
        if kind is None:
            return {
                "success": False,
                "error": (
                    f"unsupported office extension {ext!r}. "
                    f"Expected one of: {', '.join(OFFICE_EXT_TO_KIND)}."
                ),
            }

        doc_dir = os.path.dirname(office_abs)
        if os.path.realpath(doc_dir) == workspace:
            return {
                "success": False,
                "error": (
                    "The office file sits at the workspace root, so its preview "
                    "cannot be served. Put the document in a subdirectory first "
                    "(e.g. office/<name>/document" + ext + ") and render again."
                ),
            }

        preview_abs = os.path.join(doc_dir, preview_name_for(os.path.basename(office_abs)))

        # `officecli view <file> html -o <out>` writes a static, self-contained
        # HTML snapshot (same renderer as `watch`, no server) for docx/xlsx/pptx.
        result = await self._exec(
            ["officecli", "view", office_abs, "html", "-o", preview_abs],
            cwd=cwd,
            timeout=timeout,
        )
        if not result.get("success"):
            return result
        if not os.path.isfile(preview_abs):
            return {
                "success": False,
                "error": "officecli view reported success but no preview file was written.",
            }

        return {
            "success": True,
            "office_abs": office_abs,
            "preview_abs": preview_abs,
            "office_rel": os.path.relpath(office_abs, workspace),
            "preview_rel": os.path.relpath(preview_abs, workspace),
            "kind": kind,
        }

    async def _exec(
        self,
        cmd: list[str],
        *,
        cwd: Path | str | None,
        timeout: float,
    ) -> dict:
        """Spawn officecli, collect stdout, parse JSON, handle errors.

        Resolves ``officecli`` to an absolute path and augments the child's PATH
        with the npm-global + node bin dirs, so a stripped host/MCP PATH does not
        make the binary or its ``env node`` shebang vanish.
        """
        if cmd and cmd[0] == "officecli":
            resolved, extra_path = resolve_npm_cli("officecli", "OFFICECLI_BIN")
            cmd = [resolved, *cmd[1:]]
            env = dict(os.environ)
            if extra_path:
                env["PATH"] = os.pathsep.join([*extra_path, env.get("PATH", "")])
        else:
            env = dict(os.environ)

        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=env,
                cwd=str(cwd) if cwd is not None else None,
            )
        except FileNotFoundError:
            return {
                "success": False,
                "error": (
                    "officecli not found. Install it with "
                    "`npm install -g @officecli/officecli`. If it is installed but "
                    "on a PATH this background process can't see, set OFFICECLI_BIN "
                    "to its absolute path (find it via `which officecli`)."
                ),
            }

        try:
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        except asyncio.TimeoutError:
            try:
                proc.kill()
                await proc.wait()
            except (ProcessLookupError, OSError):
                pass
            return {"success": False, "error": f"officecli timed out after {timeout}s"}

        stdout_str = stdout.decode(errors="replace").strip()
        stderr_str = stderr.decode(errors="replace").strip()

        if proc.returncode != 0:
            error_msg = stderr_str or stdout_str or f"officecli exited with code {proc.returncode}"
            try:
                parsed = json.loads(stdout_str)
                if isinstance(parsed, dict) and "error" in parsed:
                    err = parsed["error"]
                    if isinstance(err, dict):
                        error_msg = err.get("message", error_msg)
                    elif isinstance(err, str):
                        error_msg = err
            except (json.JSONDecodeError, AttributeError):
                pass
            logger.debug("officecli failed: {}", error_msg)
            return {"success": False, "error": error_msg}

        data: Any
        try:
            data = json.loads(stdout_str) if stdout_str else {}
        except json.JSONDecodeError:
            data = {"raw_output": stdout_str}

        return {"success": True, "data": data}
