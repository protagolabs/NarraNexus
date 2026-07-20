"""
@file_name: artifact_store.py
@author: NetMind.AI
@date: 2026-07-21
@description: Object storage abstraction for marketplace skill artifacts.

boto3 appears ONLY in this file (spec §4): swapping S3 for R2/OSS/GCS later
touches nothing else. Selection:
- SKILL_S3_BUCKET env set  -> S3ArtifactStore (cloud deployments)
- otherwise                -> LocalArtifactStore under
  <base_working_path>/../marketplace_store (dev / tests / single-host)
"""

import os
import shutil
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Optional

from loguru import logger


class ArtifactStore(ABC):
    """Minimal artifact interface: content-addressed puts/gets by key."""

    @abstractmethod
    def put_file(self, key: str, src: Path) -> None: ...

    @abstractmethod
    def put_bytes(self, key: str, data: bytes) -> None: ...

    @abstractmethod
    def get_to_path(self, key: str, dest: Path) -> Path: ...

    @abstractmethod
    def exists(self, key: str) -> bool: ...


class LocalArtifactStore(ArtifactStore):
    """Filesystem-backed store (dev, tests, single-host cloud fallback)."""

    def __init__(self, root: Path):
        self.root = Path(root)

    def _path(self, key: str) -> Path:
        path = (self.root / key).resolve()
        if self.root.resolve() not in path.parents:
            raise ValueError(f"Artifact key escapes the store root: {key!r}")
        return path

    def put_file(self, key: str, src: Path) -> None:
        dest = self._path(key)
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(src, dest)

    def put_bytes(self, key: str, data: bytes) -> None:
        dest = self._path(key)
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(data)

    def get_to_path(self, key: str, dest: Path) -> Path:
        src = self._path(key)
        if not src.exists():
            raise FileNotFoundError(f"Artifact not found: {key}")
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(src, dest)
        return dest

    def exists(self, key: str) -> bool:
        return self._path(key).exists()


class S3ArtifactStore(ArtifactStore):
    """S3-backed store. The boto3 client is created lazily so importing this
    module never requires AWS credentials."""

    def __init__(self, bucket: str, prefix: str = "", region: Optional[str] = None):
        self.bucket = bucket
        self.prefix = prefix.strip("/")
        self.region = region
        self._client = None

    def _s3(self):
        if self._client is None:
            import boto3

            self._client = boto3.client("s3", region_name=self.region)
        return self._client

    def _key(self, key: str) -> str:
        return f"{self.prefix}/{key}" if self.prefix else key

    def put_file(self, key: str, src: Path) -> None:
        self._s3().upload_file(str(src), self.bucket, self._key(key))

    def put_bytes(self, key: str, data: bytes) -> None:
        self._s3().put_object(Bucket=self.bucket, Key=self._key(key), Body=data)

    def get_to_path(self, key: str, dest: Path) -> Path:
        dest.parent.mkdir(parents=True, exist_ok=True)
        self._s3().download_file(self.bucket, self._key(key), str(dest))
        return dest

    def exists(self, key: str) -> bool:
        try:
            self._s3().head_object(Bucket=self.bucket, Key=self._key(key))
            return True
        except Exception:
            return False


def get_artifact_store() -> ArtifactStore:
    bucket = os.environ.get("SKILL_S3_BUCKET")
    if bucket:
        return S3ArtifactStore(
            bucket=bucket,
            prefix=os.environ.get("SKILL_S3_PREFIX", "narranexus-skills"),
            region=os.environ.get("SKILL_S3_REGION"),
        )
    from xyz_agent_context.settings import settings

    root = Path(settings.base_working_path).parent / "marketplace_store"
    logger.debug(f"ArtifactStore: SKILL_S3_BUCKET unset, using local store at {root}")
    return LocalArtifactStore(root)
