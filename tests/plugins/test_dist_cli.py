"""
@file_name: test_dist_cli.py
@author: Bin Liang
@date: 2026-09-04
@description: `narranexus dist doctor` prints the resolution and exits non-zero on problems; `narranexus dist lock` writes the lock file.
"""
from __future__ import annotations

import json
from pathlib import Path

from narranexus.cli.main import main

REPO = Path(__file__).resolve().parents[2]


def test_doctor_reports_and_exit_codes(tmp_path: Path, capsys):
    assert main(["dist", "doctor", str(REPO / "distributions" / "minimal")]) == 0
    out = capsys.readouterr().out
    assert "narranexus.minimal" in out and out.strip().endswith("ok")
    assert main(["dist", "doctor", str(REPO / "distributions" / "cloud"), "--json"]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["ok"] and report["auth"] == "builtin.auth.netmind"
    (tmp_path / "narranexus-dist.json").write_text(json.dumps({"id": "acme.bad", "displayName": "b", "plugins": {"ghost.x": "*"}}))
    assert main(["dist", "doctor", str(tmp_path)]) == 1
    assert "PROBLEM: ghost.x: unknown plugin" in capsys.readouterr().out
    assert main(["dist", "doctor", str(tmp_path / "missing")]) == 1
    assert "not found" in capsys.readouterr().err


def test_lock_writes_next_to_the_declaration(tmp_path: Path, capsys):
    src = REPO / "distributions" / "example-tob"
    out = tmp_path / "lock.json"
    assert main(["dist", "lock", str(src), "--out", str(out)]) == 0
    assert capsys.readouterr().out.strip() == str(out)
    assert json.loads(out.read_text())["auth"] == "acme.auth-sso"
