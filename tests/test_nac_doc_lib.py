"""
@file_name: test_nac_doc_lib.py
@author: NexusAgent
@date: 2026-04-09
@description: Tests for scripts.nac_doc_lib — rule evaluation, frontmatter, path helpers.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from scripts import nac_doc_lib
from scripts.nac_doc_lib import (
    is_empty_or_pure_reexport_init,
    is_excluded_dir,
    is_overview_only_dir,
    parse_frontmatter,
    render_frontmatter,
    walk_source_trees,
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


def test_parse_frontmatter_strips_double_quotes() -> None:
    md = '---\nstub: "true"\ncode_file: "src/x.py"\n---\n'
    fm, _ = parse_frontmatter(md)
    assert fm == {"stub": "true", "code_file": "src/x.py"}


def test_parse_frontmatter_strips_single_quotes() -> None:
    md = "---\nstub: 'false'\n---\n"
    fm, _ = parse_frontmatter(md)
    assert fm == {"stub": "false"}


def test_parse_frontmatter_keeps_unmatched_quote() -> None:
    """A lone quote is not a wrapper — keep it as-is."""
    md = "---\nname: O'Brien\n---\n"
    fm, _ = parse_frontmatter(md)
    assert fm == {"name": "O'Brien"}


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


# ============================================================================
# walk_source_trees direct tests
# ============================================================================


@pytest.fixture
def fake_repo_for_walk(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """
    Build a synthetic repo with all the interesting edge cases so walk_source_trees
    can be tested in isolation (without going through scaffold).
    """
    src = tmp_path / "src" / "pkg"
    src.mkdir(parents=True)

    # Normal code file → required
    (src / "real.py").write_text("def foo(): pass\n", encoding="utf-8")

    # Pure re-export __init__ → excluded (regression for the ast fix)
    (src / "__init__.py").write_text(
        '"""Pkg."""\nfrom .real import (\n    foo,\n)\n', encoding="utf-8"
    )

    # Subdirectory with its own __init__.py that has real logic → included
    (src / "sub").mkdir()
    (src / "sub" / "__init__.py").write_text(
        "from .inner import Inner\nMODULE_MAP = {'inner': Inner}\n", encoding="utf-8"
    )
    (src / "sub" / "inner.py").write_text("class Inner: pass\n", encoding="utf-8")

    # Overview-only dir (_*_impl) → overview required, internals skipped
    (src / "_guts_impl").mkdir()
    (src / "_guts_impl" / "secret.py").write_text("x = 1\n", encoding="utf-8")

    # Build artifact → entirely skipped
    (src / "__pycache__").mkdir()
    (src / "__pycache__" / "x.pyc").write_text("", encoding="utf-8")

    # Non-.py file → skipped
    (src / "notes.txt").write_text("hello", encoding="utf-8")

    monkeypatch.setattr(nac_doc_lib, "INCLUDE_SPECS", (
        nac_doc_lib.IncludeSpec(root="src/pkg", extensions=(".py",)),
    ))
    monkeypatch.setattr(nac_doc_lib, "repo_root", lambda: tmp_path)
    return tmp_path


def test_walk_required_files_excludes_pure_reexport_init(fake_repo_for_walk: Path) -> None:
    plan = walk_source_trees()
    rels = {p.relative_to(fake_repo_for_walk).as_posix() for p in plan.required_file_mds}
    assert "src/pkg/real.py" in rels
    assert "src/pkg/sub/inner.py" in rels
    assert "src/pkg/sub/__init__.py" in rels  # has MODULE_MAP logic
    # Pure re-export __init__ must NOT be required
    assert "src/pkg/__init__.py" not in rels


def test_walk_required_files_excludes_overview_only_internals(fake_repo_for_walk: Path) -> None:
    plan = walk_source_trees()
    rels = {p.relative_to(fake_repo_for_walk).as_posix() for p in plan.required_file_mds}
    assert "src/pkg/_guts_impl/secret.py" not in rels


def test_walk_required_dirs_includes_overview_only_dir(fake_repo_for_walk: Path) -> None:
    plan = walk_source_trees()
    rels = {p.relative_to(fake_repo_for_walk).as_posix() for p in plan.required_dir_overviews}
    assert "src/pkg" in rels
    assert "src/pkg/sub" in rels
    assert "src/pkg/_guts_impl" in rels  # overview-only dir still needs its _overview.md


def test_walk_prunes_pycache(fake_repo_for_walk: Path) -> None:
    plan = walk_source_trees()
    rels = {p.relative_to(fake_repo_for_walk).as_posix() for p in plan.required_dir_overviews}
    assert not any("__pycache__" in r for r in rels)


def test_walk_ignores_non_matching_extensions(fake_repo_for_walk: Path) -> None:
    plan = walk_source_trees()
    rels = {p.relative_to(fake_repo_for_walk).as_posix() for p in plan.required_file_mds}
    assert not any(r.endswith(".txt") for r in rels)
