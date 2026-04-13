"""
@file_name: test_audit_nac_doc.py
@author: NexusAgent
@date: 2026-04-09
@description: Tests for scripts/audit_nac_doc.py — Layer 3 soft staleness detection.

The audit script uses git log to detect when a code file has been modified
after its mirror md's `last_verified` date. We test using a real git repo
inside tmp_path to avoid brittle mocking.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from scripts import nac_doc_lib
from scripts.audit_nac_doc import audit


def _run(cmd: list[str], cwd: Path) -> None:
    subprocess.run(cmd, cwd=cwd, check=True, capture_output=True)


@pytest.fixture
def fake_git_repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Build a minimal git repo with a code file and a mirror md."""
    _run(["git", "init", "-q", "-b", "main"], tmp_path)
    _run(["git", "config", "user.email", "test@test"], tmp_path)
    _run(["git", "config", "user.name", "test"], tmp_path)

    src = tmp_path / "src" / "pkg"
    src.mkdir(parents=True)
    code = src / "real.py"
    code.write_text("def foo(): pass\n", encoding="utf-8")

    mirror = tmp_path / ".nac_doc" / "mirror" / "src" / "pkg"
    mirror.mkdir(parents=True)
    md = mirror / "real.py.md"
    md.write_text(
        "---\ncode_file: src/pkg/real.py\nlast_verified: 2020-01-01\nstub: false\n---\n\n# real.py\nintent body\n",
        encoding="utf-8",
    )

    _run(["git", "add", "."], tmp_path)
    _run(["git", "commit", "-q", "-m", "initial"], tmp_path)

    monkeypatch.setattr(nac_doc_lib, "INCLUDE_SPECS", (
        nac_doc_lib.IncludeSpec(root="src/pkg", extensions=(".py",)),
    ))
    monkeypatch.setattr(nac_doc_lib, "repo_root", lambda: tmp_path)
    monkeypatch.setattr(nac_doc_lib, "mirror_root", lambda: tmp_path / ".nac_doc" / "mirror")
    return tmp_path


def test_audit_reports_stale_md_after_code_change(fake_git_repo: Path) -> None:
    # Modify the code file AFTER last_verified
    code = fake_git_repo / "src/pkg/real.py"
    code.write_text("def foo(): return 42\n", encoding="utf-8")
    _run(["git", "add", "."], fake_git_repo)
    _run(["git", "commit", "-q", "-m", "update foo"], fake_git_repo)

    stale = audit()
    assert any("real.py" in entry.rel_code for entry in stale)


def test_audit_does_not_report_fresh_md(fake_git_repo: Path) -> None:
    # Update last_verified to today
    from datetime import date
    md = fake_git_repo / ".nac_doc/mirror/src/pkg/real.py.md"
    md.write_text(
        f"---\ncode_file: src/pkg/real.py\nlast_verified: {date.today().isoformat()}\nstub: false\n---\n\nbody\n",
        encoding="utf-8",
    )
    # Now modify code and commit AFTER updating last_verified
    code = fake_git_repo / "src/pkg/real.py"
    code.write_text("def foo(): return 1\n", encoding="utf-8")
    _run(["git", "add", "."], fake_git_repo)
    _run(["git", "commit", "-q", "-m", "tweak"], fake_git_repo)

    stale = audit()
    assert all("real.py" not in entry.rel_code for entry in stale)


def test_audit_skips_stub_mds(fake_git_repo: Path) -> None:
    # Flip md to stub: true — stubs are reported separately, not as stale
    md = fake_git_repo / ".nac_doc/mirror/src/pkg/real.py.md"
    md.write_text(
        "---\ncode_file: src/pkg/real.py\nlast_verified: 2020-01-01\nstub: true\n---\n",
        encoding="utf-8",
    )
    stale = audit()
    assert all("real.py" not in entry.rel_code for entry in stale)
