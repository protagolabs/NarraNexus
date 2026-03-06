"""
@file_name: agents_files.py
@author: NetMind.AI
@date: 2025-11-28
@description: Agent workspace file management routes

Provides endpoints for:
- GET /{agent_id}/files - List workspace files
- POST /{agent_id}/files - Upload file to workspace
- DELETE /{agent_id}/files/{filename} - Delete workspace file
"""

import os

from fastapi import APIRouter, Query, UploadFile, File
from loguru import logger

from xyz_agent_context.schema import (
    FileInfo,
    FileListResponse,
    FileUploadResponse,
    FileDeleteResponse,
)


router = APIRouter()


def _get_workspace_path(agent_id: str, user_id: str) -> str:
    """Get Agent-User workspace path"""
    from xyz_agent_context.settings import settings
    base_path = settings.base_working_path
    return os.path.join(base_path, f"{agent_id}_{user_id}")


@router.get("/{agent_id}/files", response_model=FileListResponse)
async def list_workspace_files(
    agent_id: str,
    user_id: str = Query(..., description="User ID")
):
    """List all files in Agent workspace"""
    logger.info(f"Listing files for agent: {agent_id}, user: {user_id}")

    try:
        workspace_path = _get_workspace_path(agent_id, user_id)

        if not os.path.exists(workspace_path):
            return FileListResponse(success=True, files=[], workspace_path=workspace_path)

        files = []
        for filename in os.listdir(workspace_path):
            filepath = os.path.join(workspace_path, filename)
            if os.path.isfile(filepath):
                stat = os.stat(filepath)
                files.append(FileInfo(
                    filename=filename,
                    size=stat.st_size,
                    modified_at=str(stat.st_mtime),
                ))

        files.sort(key=lambda f: f.modified_at, reverse=True)

        return FileListResponse(
            success=True, files=files, workspace_path=workspace_path
        )

    except Exception as e:
        logger.error(f"Error listing files: {e}")
        return FileListResponse(success=False, error=str(e))


@router.post("/{agent_id}/files", response_model=FileUploadResponse)
async def upload_file(
    agent_id: str,
    user_id: str = Query(..., description="User ID"),
    file: UploadFile = File(..., description="File to upload"),
):
    """Upload file to Agent workspace"""
    logger.info(f"Uploading file '{file.filename}' for agent: {agent_id}, user: {user_id}")

    try:
        # Security check: prevent path traversal attacks
        safe_filename = os.path.basename(file.filename)
        if safe_filename != file.filename or '..' in file.filename:
            return FileUploadResponse(
                success=False,
                error="Invalid filename: path traversal not allowed"
            )

        workspace_path = _get_workspace_path(agent_id, user_id)

        if not os.path.exists(workspace_path):
            os.makedirs(workspace_path)
            logger.info(f"Created workspace directory: {workspace_path}")

        filepath = os.path.join(workspace_path, safe_filename)
        content = await file.read()

        with open(filepath, "wb") as f:
            f.write(content)

        file_size = len(content)
        logger.info(f"File saved: {filepath} ({file_size} bytes)")

        return FileUploadResponse(
            success=True,
            filename=file.filename,
            size=file_size,
            workspace_path=workspace_path,
        )

    except Exception as e:
        logger.error(f"Error uploading file: {e}")
        return FileUploadResponse(success=False, error=str(e))


@router.delete("/{agent_id}/files/{filename}", response_model=FileDeleteResponse)
async def delete_file(
    agent_id: str,
    filename: str,
    user_id: str = Query(..., description="User ID"),
):
    """Delete file from Agent workspace"""
    logger.info(f"Deleting file '{filename}' for agent: {agent_id}, user: {user_id}")

    try:
        # Security check: prevent path traversal attacks
        if os.path.basename(filename) != filename or '..' in filename:
            return FileDeleteResponse(
                success=False,
                error="Invalid filename: path traversal not allowed"
            )

        workspace_path = _get_workspace_path(agent_id, user_id)
        filepath = os.path.join(workspace_path, filename)

        # Secondary security check: ensure file path is within workspace
        if not os.path.abspath(filepath).startswith(os.path.abspath(workspace_path)):
            return FileDeleteResponse(
                success=False,
                error="Invalid filename: path traversal not allowed"
            )

        if not os.path.exists(filepath):
            return FileDeleteResponse(
                success=False,
                error=f"File not found: {filename}"
            )

        os.remove(filepath)
        logger.info(f"File deleted: {filepath}")

        return FileDeleteResponse(success=True, filename=filename)

    except Exception as e:
        logger.error(f"Error deleting file: {e}")
        return FileDeleteResponse(success=False, error=str(e))
