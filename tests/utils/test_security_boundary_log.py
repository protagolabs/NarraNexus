"""
@file_name: test_security_boundary_log.py
@author: Bin Liang
@date: 2026-10-30
@description: The boundary-violation log line is a contract with an external
log watcher: fixed marker, fixed field order, one line no matter what the
attacker-controlled detail contains.
"""
from __future__ import annotations

import pytest
from loguru import logger

from narranexus.platform.utils.logging import log_boundary_violation
from narranexus.platform.utils.logging._security import BOUNDARY_MARKER


@pytest.fixture
def lines():
    captured: list[str] = []
    sink_id = logger.add(captured.append, level="WARNING", format="{message}")
    yield captured
    logger.remove(sink_id)


def test_line_shape_is_stable(lines):
    log_boundary_violation("bundle_import.preflight", actor="u1", detail="../x")
    assert len(lines) == 1
    assert lines[0].strip() == (
        f"{BOUNDARY_MARKER} surface=bundle_import.preflight actor=u1 owner=- detail='../x'"
    )


def test_missing_ids_render_as_dash(lines):
    log_boundary_violation("artifact.heal_symlink", owner="u2", detail="p")
    assert " actor=- owner=u2 " in lines[0]


def test_an_id_with_whitespace_stays_one_token(lines):
    log_boundary_violation("s", actor="legacy user", detail="d")
    assert " actor=legacy%20user owner=- " in lines[0]


def test_detail_cannot_forge_a_second_line(lines):
    log_boundary_violation("s", actor="u1", detail="a\n[security.boundary] surface=fake")
    text = lines[0].rstrip("\n")
    assert "\n" not in text
    # The genuine fields always come first; the forged text stays inside the
    # quoted detail, so a watcher reading the first surface= sees the real one.
    assert text.startswith(f"{BOUNDARY_MARKER} surface=s actor=u1 owner=- detail='")


def test_detail_is_bounded(lines):
    log_boundary_violation("s", detail="x" * 5000)
    assert len(lines[0]) < 500
    assert lines[0].rstrip().endswith("...(truncated)")
