"""
@file_name: test_mysql_gate_single_source.py
@author: NarraNexus
@date: 2026-08-17
@description: Every MySQL dialect twin must gate on the same env var name.

`tests/mysql_dialect.py` exists so that "run this against a real MySQL" is
described in one place, and its docstring is honest that it did not finish the
job: only one of the nine twins imports it, the other eight each keep their own
`MYSQL_URL_ENV = "NARRANEXUS_MYSQL_TEST_URL"`.

Nine copies of a string are fine right up until one of them changes. The
failure mode is the nasty kind: rename the env var in the helper (or in CI) and
the eight copies stop matching, their `skipif` fires, and **eight dialect twins
report green while executing nothing**. That is the exact regression the
`backend-tests` CI job was added to prevent, arriving through the one door the
job cannot see — a skip is not a failure.

So until the copies are migrated, the invariant is enforced from outside: every
twin's gate names the same variable, and the CI workflow passes that same name.
This test needs no MySQL and no container; it reads files.

Whoever migrates the eight into `tests.mysql_dialect` should keep this test —
it then guards the helper against CI drifting away from it, which is the half
that survives the refactor.
"""
from __future__ import annotations

import re
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]
_TESTS = _REPO / "tests"
_WORKFLOW = _REPO / ".github/workflows/ci.yml"

# The single source of truth, by definition: whatever the shared helper says.
_HELPER = _TESTS / "mysql_dialect.py"
_ENV_IN_HELPER = re.search(
    r'^MYSQL_URL_ENV\s*=\s*"([^"]+)"', _HELPER.read_text(), re.M
)


def _twins() -> list[Path]:
    return sorted(_TESTS.rglob("*_mysql.py"))


def test_the_helper_still_declares_the_env_var_name():
    """Everything below is measured against this one line, so its absence has to
    be a failure here rather than a silently empty comparison."""
    assert _ENV_IN_HELPER is not None, (
        f"{_HELPER.relative_to(_REPO)} no longer declares MYSQL_URL_ENV — this "
        f"file's whole premise was that it is the canonical name"
    )


def test_there_are_dialect_twins_to_check():
    """A glob that matches nothing passes every assertion about its members. If
    the twins are ever renamed out of the `*_mysql.py` shape, this test is the
    thing that notices instead of the suite quietly checking nothing."""
    assert len(_twins()) >= 9, f"expected the nine dialect twins, found {_twins()}"


def test_every_twin_gates_on_the_canonical_env_var_name():
    """Eight of the nine hold their own copy of the name. A copy that drifts does
    not fail — it skips, and a skip is green."""
    canonical = _ENV_IN_HELPER.group(1)
    offenders: dict[str, list[str]] = {}

    for twin in _twins():
        text = twin.read_text()
        names = set(re.findall(r'MYSQL_URL_ENV\s*=\s*"([^"]+)"', text))
        # A twin that imports the helper declares nothing of its own, which is
        # the end state we want; it inherits the canonical name by construction.
        imports_helper = "mysql_dialect import" in text or "mysql_dialect" in text
        if not names and imports_helper:
            continue
        wrong = sorted(n for n in names if n != canonical)
        if wrong or (not names and not imports_helper):
            offenders[str(twin.relative_to(_REPO))] = wrong or ["<no gate at all>"]

    assert not offenders, (
        f"these dialect twins do not gate on {canonical!r}, so they will skip "
        f"(and report green) wherever it is configured: {offenders}"
    )


def test_ci_passes_the_same_env_var_name_the_twins_read():
    """The other end of the same wire. CI can be right about the URL and still
    set it under a name nothing reads — nine skips, one green job, nobody
    the wiser."""
    canonical = _ENV_IN_HELPER.group(1)
    workflow = _WORKFLOW.read_text()

    assert f"{canonical}:" in workflow, (
        f"{_WORKFLOW.relative_to(_REPO)} does not set {canonical} — the dialect "
        f"twins would skip in CI, which looks exactly like passing"
    )
