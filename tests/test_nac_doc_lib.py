"""
@file_name: test_nac_doc_lib.py
@author: NexusAgent
@date: 2026-04-09
@description: Tests for scripts.nac_doc_lib — rule evaluation, frontmatter, path helpers.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from scripts.nac_doc_lib import (
    is_empty_or_pure_reexport_init,
    is_excluded_dir,
    is_overview_only_dir,
    parse_frontmatter,
    render_frontmatter,
)


def test_parse_frontmatter_simple() -> None:
    md = "---\ncode_file: src/foo.py\nlast_verified: 2026-04-09\n---\n\n# Body\n"
    fm, body = parse_frontmatter(md)
    assert fm == {"code_file": "src/foo.py", "last_verified": "2026-04-09"}
    assert body.strip() == "# Body"


def test_parse_frontmatter_absent() -> None:
    md = "# Just a body\n"
    fm, body = parse_frontmatter(md)
    assert fm == {}
    assert body == md


def test_render_frontmatter_roundtrip() -> None:
    fm = {"code_file": "x.py", "last_verified": "2026-04-09"}
    rendered = render_frontmatter(fm)
    parsed, _ = parse_frontmatter(rendered + "\nbody")
    assert parsed == fm


def test_is_overview_only_dir_matches_impl_pattern(tmp_path: Path) -> None:
    d = tmp_path / "_module_impl"
    d.mkdir()
    assert is_overview_only_dir(d) is True


def test_is_overview_only_dir_rejects_normal_dir(tmp_path: Path) -> None:
    d = tmp_path / "module"
    d.mkdir()
    assert is_overview_only_dir(d) is False


def test_is_excluded_dir_pycache(tmp_path: Path) -> None:
    d = tmp_path / "__pycache__"
    d.mkdir()
    assert is_excluded_dir(d) is True


def test_empty_init_is_pure_reexport(tmp_path: Path) -> None:
    f = tmp_path / "__init__.py"
    f.write_text("", encoding="utf-8")
    assert is_empty_or_pure_reexport_init(f) is True


def test_reexport_only_init_is_pure_reexport(tmp_path: Path) -> None:
    f = tmp_path / "__init__.py"
    f.write_text(
        '"""Module docstring."""\n'
        "from .foo import bar\n"
        "from .baz import qux\n"
        "__all__ = ['bar', 'qux']\n",
        encoding="utf-8",
    )
    assert is_empty_or_pure_reexport_init(f) is True


def test_init_with_logic_is_not_pure_reexport(tmp_path: Path) -> None:
    f = tmp_path / "__init__.py"
    f.write_text(
        "from .foo import Foo\n\nMODULE_MAP = {'foo': Foo}\n",
        encoding="utf-8",
    )
    assert is_empty_or_pure_reexport_init(f) is False


def test_non_init_is_not_pure_reexport(tmp_path: Path) -> None:
    f = tmp_path / "foo.py"
    f.write_text("", encoding="utf-8")
    assert is_empty_or_pure_reexport_init(f) is False


def test_multiline_parenthesized_import_is_pure_reexport(tmp_path: Path) -> None:
    """Regression: real-world schema/__init__.py uses multi-line imports."""
    f = tmp_path / "__init__.py"
    f.write_text(
        '"""Schema package."""\n'
        "from .module_schema import (\n"
        "    ModuleConfig,\n"
        "    ContextData,\n"
        ")\n"
        "from .event_schema import (\n"
        "    Event,\n"
        "    EventType,\n"
        ")\n",
        encoding="utf-8",
    )
    assert is_empty_or_pure_reexport_init(f) is True


def test_init_with_alias_assignment_is_not_pure_reexport(tmp_path: Path) -> None:
    """A non-__all__ assignment (e.g. DatabaseClient = AsyncDatabaseClient) counts as logic."""
    f = tmp_path / "__init__.py"
    f.write_text(
        "from .database import AsyncDatabaseClient\n"
        "DatabaseClient = AsyncDatabaseClient\n",
        encoding="utf-8",
    )
    assert is_empty_or_pure_reexport_init(f) is False


def test_init_with_function_def_is_not_pure_reexport(tmp_path: Path) -> None:
    f = tmp_path / "__init__.py"
    f.write_text(
        "from .foo import bar\n"
        "def helper(): return bar()\n",
        encoding="utf-8",
    )
    assert is_empty_or_pure_reexport_init(f) is False


def test_init_with_syntax_error_is_not_pure_reexport(tmp_path: Path) -> None:
    f = tmp_path / "__init__.py"
    f.write_text("from .foo import (", encoding="utf-8")  # unterminated
    assert is_empty_or_pure_reexport_init(f) is False


def test_init_with_all_and_multiline_import(tmp_path: Path) -> None:
    f = tmp_path / "__init__.py"
    f.write_text(
        '"""Package."""\n'
        "from .a import (X, Y)\n"
        "__all__ = [\n"
        "    'X',\n"
        "    'Y',\n"
        "]\n",
        encoding="utf-8",
    )
    assert is_empty_or_pure_reexport_init(f) is True
