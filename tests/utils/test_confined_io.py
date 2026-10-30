"""
@file_name: test_confined_io.py
@author: Bin Liang
@date: 2026-10-30
@description: Unit tests for utils/confined_io.py — fd-relative file operations
that make "prove the path is inside the root" and "open it" ONE operation.

The attack these pin: a tenant controls the contents of its own workspace and
can create symlinks there. A check-then-open-by-string sequence can be raced
(a directory component swapped for a link between the two steps). The helper
walks every component with O_NOFOLLOW, so a link anywhere on the path —
including one planted AFTER any earlier check — is refused instead of followed.
"""
from __future__ import annotations

import os

import pytest

from narranexus.platform.utils import confined_io as cio


@pytest.fixture
def tree(tmp_path):
    root = tmp_path / "root"
    (root / "a" / "b").mkdir(parents=True)
    (root / "a" / "b" / "f.txt").write_bytes(b"inside")
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret.txt").write_bytes(b"outside-secret")
    return {"root": str(root), "outside": outside, "tmp": tmp_path}


def _read(root, rel):
    with cio.open_read(root, rel) as f:
        return f.read()


# ── path syntax ──────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "rel",
    ["", "/etc/passwd", "../x", "a/../../x", "a/./b", "a//b", "a/b/", "a\x00b", "."],
)
def test_malformed_relative_paths_are_refused(tree, rel):
    with pytest.raises(cio.ConfinedPathError):
        cio.open_read(tree["root"], rel)


def test_relative_root_is_refused(tree):
    with pytest.raises(cio.ConfinedPathError):
        cio.open_read("root", "a/b/f.txt")


# ── read ─────────────────────────────────────────────────────────────────────


def test_reads_a_normal_nested_file(tree):
    assert _read(tree["root"], "a/b/f.txt") == b"inside"


def test_missing_file_is_file_not_found(tree):
    with pytest.raises(FileNotFoundError):
        cio.open_read(tree["root"], "a/b/nope.txt")


def test_directory_leaf_is_not_a_regular_file(tree):
    with pytest.raises(cio.NotARegularFileError):
        cio.open_read(tree["root"], "a/b")


def test_fifo_leaf_is_refused_without_blocking(tree):
    os.mkfifo(os.path.join(tree["root"], "a", "pipe"))
    with pytest.raises(cio.NotARegularFileError):
        cio.open_read(tree["root"], "a/pipe")


def test_symlink_leaf_is_refused(tree):
    os.symlink(tree["outside"] / "secret.txt", os.path.join(tree["root"], "a", "b", "link"))
    with pytest.raises(cio.SymlinkRefusedError):
        cio.open_read(tree["root"], "a/b/link")


def test_symlink_middle_component_is_refused(tree):
    os.symlink(tree["outside"], os.path.join(tree["root"], "a", "evil"))
    with pytest.raises(cio.SymlinkRefusedError):
        cio.open_read(tree["root"], "a/evil/secret.txt")


def test_dangling_symlink_component_is_refused_not_missing(tree):
    os.symlink(tree["tmp"] / "does-not-exist", os.path.join(tree["root"], "a", "dangling"))
    with pytest.raises(cio.SymlinkRefusedError):
        cio.open_read(tree["root"], "a/dangling/x")


def test_symlink_root_is_refused(tree):
    link = tree["tmp"] / "rootlink"
    os.symlink(tree["root"], link)
    with pytest.raises(cio.SymlinkRefusedError):
        cio.open_read(str(link), "a/b/f.txt")


def test_swap_after_check_is_refused(tree):
    """The race itself: the path is fine, then a directory on it becomes a link
    to another tree, then the open happens. The old string-reopen followed it."""
    root = tree["root"]
    assert _read(root, "a/b/f.txt") == b"inside"
    (tree["outside"] / "f.txt").write_bytes(b"victim")
    os.rename(os.path.join(root, "a", "b"), os.path.join(root, "a", "b.moved"))
    os.symlink(tree["outside"], os.path.join(root, "a", "b"))
    with pytest.raises(cio.SymlinkRefusedError):
        cio.open_read(root, "a/b/f.txt")
    # The pre-fix behaviour, for contrast: the string path now reads the victim.
    with open(os.path.join(root, "a", "b", "f.txt"), "rb") as f:
        assert f.read() == b"victim"


# ── atomic write ─────────────────────────────────────────────────────────────


def test_write_atomic_replaces_content_and_leaves_no_temp(tree):
    cio.write_atomic(tree["root"], "a/b/f.txt", b"new")
    assert _read(tree["root"], "a/b/f.txt") == b"new"
    assert sorted(os.listdir(os.path.join(tree["root"], "a", "b"))) == ["f.txt"]


def test_write_atomic_creates_a_new_file(tree):
    cio.write_atomic(tree["root"], "a/b/new.txt", b"x")
    assert _read(tree["root"], "a/b/new.txt") == b"x"


def test_write_atomic_keeps_existing_permission_bits(tree):
    p = os.path.join(tree["root"], "a", "b", "f.txt")
    os.chmod(p, 0o640)
    cio.write_atomic(tree["root"], "a/b/f.txt", b"new")
    assert os.stat(p).st_mode & 0o777 == 0o640


def test_write_atomic_refuses_symlinked_directory_and_writes_nothing(tree):
    os.symlink(tree["outside"], os.path.join(tree["root"], "a", "evil"))
    with pytest.raises(cio.SymlinkRefusedError):
        cio.write_atomic(tree["root"], "a/evil/pwn.txt", b"pwn")
    assert sorted(os.listdir(tree["outside"])) == ["secret.txt"]


def test_write_atomic_over_a_symlink_leaf_replaces_the_link_not_the_target(tree):
    target = tree["outside"] / "secret.txt"
    os.symlink(target, os.path.join(tree["root"], "a", "b", "link"))
    cio.write_atomic(tree["root"], "a/b/link", b"mine")
    assert target.read_bytes() == b"outside-secret"
    assert not os.path.islink(os.path.join(tree["root"], "a", "b", "link"))
    assert _read(tree["root"], "a/b/link") == b"mine"


def test_write_atomic_failure_cleans_the_temp_file(tree, monkeypatch):
    def boom(*a, **k):
        raise OSError("replace failed")

    monkeypatch.setattr(cio.os, "replace", boom)
    with pytest.raises(OSError):
        cio.write_atomic(tree["root"], "a/b/f.txt", b"new")
    assert sorted(os.listdir(os.path.join(tree["root"], "a", "b"))) == ["f.txt"]
    assert _read(tree["root"], "a/b/f.txt") == b"inside"


def test_write_atomic_missing_parent_without_make_parents_fails(tree):
    with pytest.raises(FileNotFoundError):
        cio.write_atomic(tree["root"], "x/y/z.txt", b"1")


def test_write_atomic_make_parents_creates_real_dirs(tree):
    cio.write_atomic(tree["root"], "x/y/z.txt", b"1", make_parents=True)
    assert _read(tree["root"], "x/y/z.txt") == b"1"


# ── exclusive create / unlink ────────────────────────────────────────────────


def test_open_new_is_exclusive(tree):
    with cio.open_new(tree["root"], "a/b/up.bin") as f:
        f.write(b"1")
    with pytest.raises(FileExistsError):
        cio.open_new(tree["root"], "a/b/up.bin")


def test_open_new_refuses_to_write_through_a_symlink_leaf(tree):
    target = tree["outside"] / "secret.txt"
    os.symlink(target, os.path.join(tree["root"], "a", "b", "link"))
    with pytest.raises(FileExistsError):
        cio.open_new(tree["root"], "a/b/link")
    assert target.read_bytes() == b"outside-secret"


def test_open_new_refuses_symlinked_directory(tree):
    os.symlink(tree["outside"], os.path.join(tree["root"], "a", "evil"))
    with pytest.raises(cio.SymlinkRefusedError):
        cio.open_new(tree["root"], "a/evil/up.bin")
    assert sorted(os.listdir(tree["outside"])) == ["secret.txt"]


def test_unlink_removes_the_named_file_only(tree):
    cio.unlink(tree["root"], "a/b/f.txt")
    assert not os.path.exists(os.path.join(tree["root"], "a", "b", "f.txt"))


def test_unlink_does_not_follow_a_symlinked_directory(tree):
    os.symlink(tree["outside"], os.path.join(tree["root"], "a", "evil"))
    with pytest.raises(cio.SymlinkRefusedError):
        cio.unlink(tree["root"], "a/evil/secret.txt")
    assert (tree["outside"] / "secret.txt").exists()


# ── directories ──────────────────────────────────────────────────────────────


def test_make_dirs_creates_nested_real_directories(tree):
    cio.make_dirs(tree["root"], "tabs/abc12345")
    assert os.path.isdir(os.path.join(tree["root"], "tabs", "abc12345"))


def test_make_dirs_is_idempotent(tree):
    cio.make_dirs(tree["root"], "a/b")
    cio.make_dirs(tree["root"], "a/b")


def test_make_dirs_does_not_recurse_through_a_link(tree):
    os.symlink(tree["outside"], os.path.join(tree["root"], "tabs"))
    with pytest.raises(cio.SymlinkRefusedError):
        cio.make_dirs(tree["root"], "tabs/abc12345")
    assert sorted(os.listdir(tree["outside"])) == ["secret.txt"]


def test_make_dirs_over_a_regular_file_fails(tree):
    with pytest.raises(NotADirectoryError):
        cio.make_dirs(tree["root"], "a/b/f.txt/sub")


# ── stat / size ──────────────────────────────────────────────────────────────


def test_lstat_does_not_follow_a_leaf_link(tree):
    os.symlink(tree["outside"] / "secret.txt", os.path.join(tree["root"], "a", "l"))
    import stat as st

    assert st.S_ISLNK(cio.lstat(tree["root"], "a/l").st_mode)


def test_dir_regular_bytes_counts_regular_files_only(tree):
    root = tree["root"]
    (tree["tmp"] / "big").write_bytes(b"x" * 5000)
    os.symlink(tree["tmp"] / "big", os.path.join(root, "a", "b", "biglink"))  # file link
    os.symlink(tree["outside"], os.path.join(root, "a", "dirlink"))  # dir link
    (os.path.join(root, "a", "c")) and os.mkdir(os.path.join(root, "a", "c"))
    with open(os.path.join(root, "a", "c", "g.txt"), "wb") as f:
        f.write(b"12345")
    # f.txt (6) + g.txt (5); neither link contributes
    assert cio.dir_regular_bytes(root, "a") == 11


def test_dir_regular_bytes_refuses_a_symlinked_start(tree):
    os.symlink(tree["outside"], os.path.join(tree["root"], "evil"))
    with pytest.raises(cio.SymlinkRefusedError):
        cio.dir_regular_bytes(tree["root"], "evil")


# ── fail closed when the platform lacks the primitives ───────────────────────


def test_fails_closed_when_dir_fd_is_unsupported(tree, monkeypatch):
    monkeypatch.setattr(cio.os, "supports_dir_fd", set())
    with pytest.raises(cio.ConfinedIOUnsupported):
        cio.open_read(tree["root"], "a/b/f.txt")
    with pytest.raises(cio.ConfinedIOUnsupported):
        cio.write_atomic(tree["root"], "a/b/f.txt", b"x")


def test_unsupported_is_also_a_confined_path_error(tree, monkeypatch):
    """Callers that map ConfinedPathError to a refusal fail closed for free."""
    monkeypatch.setattr(cio.os, "supports_dir_fd", set())
    with pytest.raises(cio.ConfinedPathError):
        cio.open_read(tree["root"], "a/b/f.txt")


# ── no fd leaks ──────────────────────────────────────────────────────────────


def _open_fd_count():
    return len(os.listdir("/proc/self/fd")) if os.path.isdir("/proc/self/fd") else None


def test_no_fd_leak_on_any_path(tree):
    before = _open_fd_count()
    if before is None:
        pytest.skip("needs /proc/self/fd")
    os.symlink(tree["outside"], os.path.join(tree["root"], "a", "evil"))
    for _ in range(20):
        _read(tree["root"], "a/b/f.txt")
        for fn in (
            lambda: cio.open_read(tree["root"], "a/evil/secret.txt"),
            lambda: cio.open_read(tree["root"], "a/b/missing"),
            lambda: cio.open_read(tree["root"], "a/b"),
            lambda: cio.write_atomic(tree["root"], "a/evil/x", b"1"),
            lambda: cio.make_dirs(tree["root"], "a/evil/x"),
            lambda: cio.dir_regular_bytes(tree["root"], "a/evil"),
        ):
            with pytest.raises(Exception):
                fn()
        cio.write_atomic(tree["root"], "a/b/f.txt", b"inside")
        cio.dir_regular_bytes(tree["root"], "a")
    assert _open_fd_count() == before


# ── split_under_base (lexical; relative / symlinked base spellings) ──────────


def test_split_under_base_returns_real_root_and_relative_tail(tree):
    root, rel = cio.split_under_base(os.path.join(tree["root"], "a", "b", "f.txt"), tree["root"])
    assert root == os.path.realpath(tree["root"])
    assert rel == "a/b/f.txt"


def test_split_under_base_refuses_a_path_outside_or_equal_to_the_base(tree):
    for bad in (str(tree["outside"] / "secret.txt"), tree["root"], tree["root"] + "-sibling/x"):
        with pytest.raises(cio.ConfinedPathError):
            cio.split_under_base(bad, tree["root"])


def test_split_under_base_collapses_dotdot_lexically(tree):
    # ".." collapses before the prefix test, so it cannot smuggle a path out
    with pytest.raises(cio.ConfinedPathError):
        cio.split_under_base(os.path.join(tree["root"], "a", "..", "..", "outside", "secret.txt"), tree["root"])


def test_split_under_base_accepts_a_symlinked_base_spelling(tree):
    link = tree["tmp"] / "baselink"
    os.symlink(tree["root"], link)
    root, rel = cio.split_under_base(str(link / "a" / "b" / "f.txt"), str(link))
    assert root == os.path.realpath(tree["root"])
    assert rel == "a/b/f.txt"
    assert _read(root, rel) == b"inside"


def test_split_under_base_accepts_a_relative_base(tree, monkeypatch):
    monkeypatch.chdir(tree["tmp"])
    root, rel = cio.split_under_base(os.path.join(tree["tmp"], "root", "a", "b", "f.txt"), "root")
    assert root == os.path.realpath(tree["root"])
    assert rel == "a/b/f.txt"


# ── remove_tree ──────────────────────────────────────────────────────────────


def test_remove_tree_deletes_a_nested_directory(tree):
    os.makedirs(os.path.join(tree["root"], "a", "b", "c", "d"))
    cio.remove_tree(tree["root"], "a")
    assert not os.path.exists(os.path.join(tree["root"], "a"))
    assert os.path.isdir(tree["root"])


def test_remove_tree_deletes_a_single_file(tree):
    cio.remove_tree(tree["root"], "a/b/f.txt")
    assert not os.path.exists(os.path.join(tree["root"], "a", "b", "f.txt"))
    assert os.path.isdir(os.path.join(tree["root"], "a", "b"))


def test_remove_tree_missing_leaf_is_file_not_found(tree):
    with pytest.raises(FileNotFoundError):
        cio.remove_tree(tree["root"], "a/nope")


def test_remove_tree_unlinks_links_inside_the_tree_without_touching_targets(tree):
    (tree["outside"] / "keep.txt").write_bytes(b"keep")
    os.symlink(tree["outside"], os.path.join(tree["root"], "a", "b", "dirlink"))
    os.symlink(tree["outside"] / "secret.txt", os.path.join(tree["root"], "a", "filelink"))
    cio.remove_tree(tree["root"], "a")
    assert not os.path.exists(os.path.join(tree["root"], "a"))
    assert sorted(os.listdir(tree["outside"])) == ["keep.txt", "secret.txt"]


def test_remove_tree_leaf_link_removes_only_the_link(tree):
    os.symlink(tree["outside"], os.path.join(tree["root"], "a", "lnk"))
    cio.remove_tree(tree["root"], "a/lnk")
    assert not os.path.lexists(os.path.join(tree["root"], "a", "lnk"))
    assert sorted(os.listdir(tree["outside"])) == ["secret.txt"]


def test_remove_tree_refuses_a_symlinked_intermediate_directory(tree):
    """The delete race: a directory on the way is a link to another tenant's
    tree. A by-path rmtree/unlink would delete the victim's data."""
    victim = tree["outside"] / "sub"
    victim.mkdir()
    (victim / "f.txt").write_bytes(b"victim")
    os.symlink(tree["outside"], os.path.join(tree["root"], "a", "evil"))
    with pytest.raises(cio.SymlinkRefusedError):
        cio.remove_tree(tree["root"], "a/evil/sub")
    with pytest.raises(cio.SymlinkRefusedError):
        cio.remove_tree(tree["root"], "a/evil/secret.txt")
    assert (victim / "f.txt").read_bytes() == b"victim"
    assert (tree["outside"] / "secret.txt").exists()


def test_remove_tree_too_deep_fails_closed(tree):
    p = os.path.join(tree["root"], "deep")
    for _ in range(12):
        p = os.path.join(p, "d")
    os.makedirs(p)
    with pytest.raises(cio.ConfinedScanError):
        cio.remove_tree(tree["root"], "deep", max_depth=5)


# ── minors: mode mask, fail-closed scan, fdopen failure ──────────────────────


def test_write_atomic_does_not_carry_setuid_bits(tree):
    p = os.path.join(tree["root"], "a", "b", "f.txt")
    os.chmod(p, 0o4755)
    cio.write_atomic(tree["root"], "a/b/f.txt", b"new")
    assert os.stat(p).st_mode & 0o7000 == 0
    assert os.stat(p).st_mode & 0o777 == 0o755


def test_dir_regular_bytes_over_depth_fails_closed(tree, monkeypatch):
    """A silent skip below the cap lets a tenant hide bytes from a size limit."""
    monkeypatch.setattr(cio, "_MAX_SCAN_DEPTH", 3)
    p = os.path.join(tree["root"], "deep")
    for _ in range(6):
        p = os.path.join(p, "d")
    os.makedirs(p)
    with open(os.path.join(p, "big.bin"), "wb") as f:
        f.write(b"x" * 100)
    with pytest.raises(cio.ConfinedScanError):
        cio.dir_regular_bytes(tree["root"], "deep")


@pytest.mark.skipif(os.geteuid() == 0, reason="root ignores directory permissions")
def test_dir_regular_bytes_unreadable_directory_fails_closed(tree):
    locked = os.path.join(tree["root"], "a", "locked")
    os.mkdir(locked)
    with open(os.path.join(locked, "hidden.bin"), "wb") as f:
        f.write(b"x" * 100)
    os.chmod(locked, 0)
    try:
        with pytest.raises(cio.ConfinedScanError):
            cio.dir_regular_bytes(tree["root"], "a")
    finally:
        os.chmod(locked, 0o755)


def test_write_atomic_closes_the_temp_fd_when_fdopen_fails(tree, monkeypatch):
    before = _open_fd_count()
    if before is None:
        pytest.skip("needs /proc/self/fd")

    def boom(*a, **k):
        raise OSError("fdopen failed")

    monkeypatch.setattr(cio.os, "fdopen", boom)
    for _ in range(5):
        with pytest.raises(OSError):
            cio.write_atomic(tree["root"], "a/b/f.txt", b"new")
    assert _open_fd_count() == before
    assert sorted(os.listdir(os.path.join(tree["root"], "a", "b"))) == ["f.txt"]
