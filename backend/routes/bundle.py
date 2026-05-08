"""
@file_name: bundle.py
@author: NetMind.AI
@date: 2026-05-08
@description: REST API for .nxbundle export/import

Subproject 2 endpoints (under /api/bundle):
- POST /export                    Build a bundle and stream it back
- POST /import/preflight          Validate + diff against this instance
- POST /import/confirm            Execute the import using a preflight token
- GET  /skills/archives           List skill archives for current user
"""

import io
import json
import shutil
import tempfile
from pathlib import Path
from typing import Optional, List, Dict, Any

from fastapi import APIRouter, HTTPException, Request, UploadFile, File, Form
from fastapi.responses import StreamingResponse, FileResponse
from loguru import logger
from pydantic import BaseModel

from xyz_agent_context.utils.db_factory import get_db_client
from xyz_agent_context.bundle.builder import ExportSelection, build_bundle
from xyz_agent_context.bundle.importer import preflight, confirm
from xyz_agent_context.repository import SkillArchiveRepository
from backend.auth import _is_cloud_mode, get_local_user_id


router = APIRouter()


async def _user_id_for_request(request: Request) -> str:
    if _is_cloud_mode():
        return request.state.user_id
    return await get_local_user_id()


class SkillExportSpec(BaseModel):
    skill_name: str
    install_method: str  # url | zip | full_copy | builtin
    source_url: Optional[str] = None
    source_type: Optional[str] = "github"
    branch: Optional[str] = "main"
    archive_path: Optional[str] = None
    manual_zip_path: Optional[str] = None


class ExportRequest(BaseModel):
    agent_ids: List[str]
    team_id: Optional[str] = None
    team_intro_md: Optional[str] = None
    skills: List[SkillExportSpec] = []
    social_entity_selection: Optional[Dict[str, List[str]]] = None
    workspace_excludes: Optional[Dict[str, List[str]]] = None
    include_chat_history: bool = True
    embedding_provider: Optional[str] = None
    embedding_model: Optional[str] = None
    embedding_dim: Optional[int] = None


@router.post("/export")
async def export_bundle(payload: ExportRequest, request: Request):
    user_id = await _user_id_for_request(request)
    if not payload.agent_ids:
        raise HTTPException(status_code=400, detail="agent_ids is required")

    out_dir = Path(tempfile.mkdtemp(prefix="nx-export-"))
    fname = f"nxbundle-{int(__import__('time').time())}.nxbundle"
    out_path = out_dir / fname

    skill_methods = {
        s.skill_name: {
            "install_method": s.install_method,
            "source_url": s.source_url,
            "source_type": s.source_type,
            "branch": s.branch,
            "archive_path": s.archive_path,
            "manual_zip_path": s.manual_zip_path,
        }
        for s in payload.skills
    }

    selection = ExportSelection(
        agent_ids=payload.agent_ids,
        team_id=payload.team_id,
        team_intro_md=payload.team_intro_md or "",
        skill_methods=skill_methods,
        social_entity_selection=payload.social_entity_selection,
        workspace_excludes=payload.workspace_excludes,
        include_chat_history=payload.include_chat_history,
        embedding_provider=payload.embedding_provider,
        embedding_model=payload.embedding_model,
        embedding_dim=payload.embedding_dim,
    )

    try:
        result = await build_bundle(user_id, selection, out_path)
    except Exception as e:
        shutil.rmtree(out_dir, ignore_errors=True)
        logger.exception("export build failed")
        raise HTTPException(status_code=500, detail=str(e))

    # Stream the file back; clean up on close.
    def iterfile():
        try:
            with open(out_path, "rb") as f:
                while True:
                    chunk = f.read(64 * 1024)
                    if not chunk:
                        break
                    yield chunk
        finally:
            shutil.rmtree(out_dir, ignore_errors=True)

    return StreamingResponse(
        iterfile(),
        media_type="application/zip",
        headers={
            "Content-Disposition": f'attachment; filename="{fname}"',
            "X-Bundle-Manifest-Sha256": result["manifest"].get("integrity_sha256", ""),
            "X-Bundle-Warnings-Count": str(len(result.get("warnings", []))),
        },
    )


@router.post("/import/preflight")
async def import_preflight(file: UploadFile = File(...), request: Request = None):
    user_id = await _user_id_for_request(request)
    tmpdir = Path(tempfile.mkdtemp(prefix="nx-preflight-"))
    bundle_path = tmpdir / (file.filename or "upload.nxbundle")

    try:
        contents = await file.read()
        bundle_path.write_bytes(contents)
        result = await preflight(bundle_path, user_id)
        return result
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.exception("preflight failed")
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        # Bundle file copy is not needed beyond preflight (the extracted dir lives elsewhere)
        try:
            bundle_path.unlink(missing_ok=True)
            tmpdir.rmdir()
        except Exception:
            pass


class ConfirmRequest(BaseModel):
    preflight_token: str


@router.post("/import/confirm")
async def import_confirm(payload: ConfirmRequest, request: Request):
    user_id = await _user_id_for_request(request)
    try:
        return await confirm(payload.preflight_token, user_id)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.exception("confirm failed")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/skills/archives")
async def list_skill_archives(request: Request):
    """List the skill archives registered for the current user."""
    user_id = await _user_id_for_request(request)
    db = await get_db_client()
    repo = SkillArchiveRepository(db)
    items = await repo.list_for_user(user_id)
    return {"archives": [a.model_dump() for a in items]}


class UploadArchiveRequest(BaseModel):
    skill_name: str
    source_type: str  # "zip" or "github"
    source_url: Optional[str] = None


@router.post("/skills/archives/upload")
async def upload_archive(
    request: Request,
    skill_name: str = Form(...),
    source_type: str = Form(...),
    source_url: Optional[str] = Form(None),
    file: Optional[UploadFile] = File(None),
):
    """Manual archive upload: user provides a zip (or a GitHub URL) for a skill that's missing an archive."""
    user_id = await _user_id_for_request(request)
    db = await get_db_client()
    repo = SkillArchiveRepository(db)

    archives_dir = Path.home() / ".nexusagent" / "skill_archives" / user_id
    archives_dir.mkdir(parents=True, exist_ok=True)

    if source_type == "github":
        if not source_url:
            raise HTTPException(status_code=400, detail="source_url required for github")
        # Defer actual tarball download to lazy time; record source.
        await repo.upsert(
            user_id=user_id,
            skill_name=skill_name,
            source_type="github",
            source_url=source_url,
            archive_path=None,
            sha256="pending",
        )
        return {"success": True, "skill_name": skill_name, "source_type": "github"}

    if source_type == "zip":
        if not file:
            raise HTTPException(status_code=400, detail="file required for zip")
        target = archives_dir / f"{skill_name}.zip"
        contents = await file.read()
        target.write_bytes(contents)
        from xyz_agent_context.bundle.security import bytes_sha256
        sha = bytes_sha256(contents)
        await repo.upsert(
            user_id=user_id,
            skill_name=skill_name,
            source_type="zip",
            source_url=None,
            archive_path=str(target),
            sha256=sha,
        )
        return {"success": True, "skill_name": skill_name, "source_type": "zip", "sha256": sha}

    raise HTTPException(status_code=400, detail="source_type must be 'github' or 'zip'")
