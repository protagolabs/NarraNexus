"""
@file_name: check_nac_doc.py
@author: NexusAgent
@date: 2026-04-09
@description: Layer 1 structural invariant checker for .nac_doc/mirror/.

Validates:
  1. Every code file matching include rules has a corresponding mirror md
     (except files in overview-only dirs and pure re-export __init__.py).
  2. Every mirror md (non _overview.md) maps to an existing code file.
  3. Every directory in the mirrored tree has an _overview.md.
  4. Each mirror md's frontmatter `code_file`/`code_dir` points to an existing path.

Exit code 0 if all invariants hold, 1 otherwise. Intended for pre-commit hook
and CI.

Run from repo root:
    uv run python -m scripts.check_nac_doc
"""

from __future__ import annotations

import sys
from pathlib import Path

from scripts import nac_doc_lib


def check() -> list[str]:
    """Run all structural checks. Returns list of error messages (empty if OK)."""
    errors: list[str] = []
    plan = nac_doc_lib.walk_source_trees()
    root = nac_doc_lib.repo_root()
    mirror = nac_doc_lib.mirror_root()

    # Invariant 1: every required code file has a mirror md
    for code_file in plan.required_file_mds:
        md = nac_doc_lib.mirror_path_for_code_file(code_file)
        if not md.exists():
            rel = code_file.relative_to(root).as_posix()
            errors.append(f"[missing mirror md] {rel} → expected {md.relative_to(root).as_posix()}")

    # Invariant 2: every required directory has an _overview.md
    for code_dir in plan.required_dir_overviews:
        md = nac_doc_lib.mirror_path_for_dir(code_dir)
        if not md.exists():
            rel = code_dir.relative_to(root).as_posix()
            errors.append(f"[missing _overview.md] {rel}/ → expected {md.relative_to(root).as_posix()}")

    # Invariant 3 + 4: every mirror md maps back to an existing code file/dir,
    # and its frontmatter path is consistent.
    if mirror.exists():
        for md in sorted(mirror.rglob("*.md")):
            _validate_md(md, errors)

    return errors


def _validate_md(md: Path, errors: list[str]) -> None:
    root = nac_doc_lib.repo_root()
    try:
        text = md.read_text(encoding="utf-8")
    except OSError as exc:
        errors.append(f"[unreadable md] {md.relative_to(root).as_posix()}: {exc}")
        return
    fm, _ = nac_doc_lib.parse_frontmatter(text)

    if md.name == "_overview.md":
        # Check it maps to an existing directory
        expected_dir = nac_doc_lib.code_dir_for_overview_path(md)
        if not expected_dir.is_dir():
            errors.append(
                f"[orphan _overview.md] {md.relative_to(root).as_posix()} → "
                f"no matching directory at {expected_dir.relative_to(root).as_posix()}"
            )
        # Frontmatter code_dir should match
        fm_dir = fm.get("code_dir")
        if fm_dir is not None:
            fm_path = root / fm_dir.rstrip("/")
            if not fm_path.is_dir():
                errors.append(
                    f"[bad frontmatter] {md.relative_to(root).as_posix()}: "
                    f"code_dir '{fm_dir}' does not exist"
                )
        return

    # Single-file md
    expected_code = nac_doc_lib.code_file_for_mirror_path(md)
    if expected_code is None:
        errors.append(f"[malformed mirror md name] {md.relative_to(root).as_posix()}")
        return
    if not expected_code.exists():
        errors.append(
            f"[orphan mirror md] {md.relative_to(root).as_posix()} → "
            f"no matching code file at {expected_code.relative_to(root).as_posix()}"
        )
    fm_file = fm.get("code_file")
    if fm_file is not None:
        fm_path = root / fm_file
        if not fm_path.exists():
            errors.append(
                f"[bad frontmatter] {md.relative_to(root).as_posix()}: "
                f"code_file '{fm_file}' does not exist"
            )


def main() -> int:
    errors = check()
    if not errors:
        print("[check_nac_doc] OK — structural invariants hold.")
        return 0
    print(f"[check_nac_doc] FAILED with {len(errors)} error(s):", file=sys.stderr)
    for e in errors:
        print(f"  - {e}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
