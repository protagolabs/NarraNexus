"""
@file_name: test_check_nac_doc.py
@author: NexusAgent
@date: 2026-04-09
@description: Integration tests for scripts/check_nac_doc.py — Layer 1 structural invariants.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from scripts import nac_doc_lib
from scripts.check_nac_doc import check
from scripts.scaffold_nac_doc import scaffold


@pytest.fixture
def fake_repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    src = tmp_path / "src" / "pkg"
    src.mkdir(parents=True)
    (src / "real.py").write_text("def foo(): pass\n", encoding="utf-8")
    (src / "utils").mkdir()
    (src / "utils" / "helper.py").write_text("x = 1\n", encoding="utf-8")
    (tmp_path / ".nac_doc" / "mirror").mkdir(parents=True)

    monkeypatch.setattr(nac_doc_lib, "INCLUDE_SPECS", (
        nac_doc_lib.IncludeSpec(root="src/pkg", extensions=(".py",)),
    ))
    monkeypatch.setattr(nac_doc_lib, "repo_root", lambda: tmp_path)
    monkeypatch.setattr(nac_doc_lib, "mirror_root", lambda: tmp_path / ".nac_doc" / "mirror")
    return tmp_path


def test_check_passes_after_scaffold(fake_repo: Path) -> None:
    scaffold()
    errors = check()
    assert errors == []


def test_check_fails_when_code_file_has_no_md(fake_repo: Path) -> None:
    scaffold()
    # Add a new code file without running scaffold
    (fake_repo / "src/pkg/new_file.py").write_text("pass\n", encoding="utf-8")
    errors = check()
    assert any("new_file.py" in e and "missing mirror md" in e for e in errors)


def test_check_fails_on_orphan_md(fake_repo: Path) -> None:
    scaffold()
    # Add an orphan md pointing to a nonexistent code file
    orphan = fake_repo / ".nac_doc/mirror/src/pkg/ghost.py.md"
    orphan.write_text(
        "---\ncode_file: src/pkg/ghost.py\nlast_verified: 2026-04-09\nstub: true\n---\n",
        encoding="utf-8",
    )
    errors = check()
    assert any("orphan" in e.lower() and "ghost.py.md" in e for e in errors)


def test_check_fails_when_dir_missing_overview(fake_repo: Path) -> None:
    scaffold()
    # Delete an _overview.md
    (fake_repo / ".nac_doc/mirror/src/pkg/utils/_overview.md").unlink()
    errors = check()
    assert any("_overview.md" in e and "missing" in e for e in errors)


def test_check_fails_on_frontmatter_pointing_to_nonexistent_file(fake_repo: Path) -> None:
    scaffold()
    # Corrupt an existing md's frontmatter to point to a ghost
    md = fake_repo / ".nac_doc/mirror/src/pkg/real.py.md"
    md.write_text(
        "---\ncode_file: src/pkg/missing.py\nlast_verified: 2026-04-09\nstub: true\n---\n",
        encoding="utf-8",
    )
    errors = check()
    assert any("frontmatter" in e.lower() and "missing.py" in e for e in errors)
