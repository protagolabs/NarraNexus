"""
@file_name: integrity.py
@author: Bin Liang
@date: 2026-09-03
@description: Asset hashing: sha256 for the registry record, SRI strings for the frontend bundle.
"""
from __future__ import annotations

import base64
import hashlib
from pathlib import Path

from narranexus.contracts import PluginError


class IntegrityError(PluginError):
    pass


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 16), b""):
            digest.update(chunk)
    return digest.hexdigest()


def verify_sha256(path: Path, expected: str) -> str:
    actual = sha256_file(path)
    if actual.lower() != expected.lower():
        raise IntegrityError(f"{path.name}: sha256 mismatch (expected {expected[:12]}…, got {actual[:12]}…)")
    return actual


def sri_for(path: Path) -> str:
    """Subresource-integrity value (``sha256-<base64>``) for a frontend asset."""
    raw = hashlib.sha256(path.read_bytes()).digest()
    return "sha256-" + base64.b64encode(raw).decode("ascii")


def hash_assets(root: Path, names: tuple[str, ...]) -> dict[str, str]:
    """``{asset name: sha256}`` for the assets that exist under ``root``."""
    out: dict[str, str] = {}
    for name in names:
        candidate = root / name
        if candidate.is_file():
            out[name] = sha256_file(candidate)
    return out


__all__ = ["IntegrityError", "hash_assets", "sha256_file", "sri_for", "verify_sha256"]
