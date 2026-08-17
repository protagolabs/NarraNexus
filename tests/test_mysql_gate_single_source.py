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

import pytest

_REPO = Path(__file__).resolve().parents[1]
_TESTS = _REPO / "tests"
_WORKFLOW = _REPO / ".github/workflows/ci.yml"
_CI_JOB = "backend-tests"

# The single source of truth, by definition: whatever the shared helper says.
_HELPER = _TESTS / "mysql_dialect.py"


def _canonical_env_name() -> str:
    """The canonical name, or a readable failure.

    Resolved lazily and through `pytest.fail`, not at import time. A module-level
    regex whose `.group(1)` the other tests reach into turns "the helper stopped
    declaring MYSQL_URL_ENV" into `AttributeError: 'NoneType' has no attribute
    'group'`, which buries the explanation this file exists to give; a
    module-level `read_text()` turns "the helper was renamed" into a collection
    error, so not even the explaining test gets to run.
    """
    if not _HELPER.exists():
        pytest.fail(
            f"{_HELPER.relative_to(_REPO)} is gone — it is where the canonical "
            f"MySQL gate env var name is declared, so there is nothing left to "
            f"check the nine dialect twins against"
        )
    found = re.search(r'^MYSQL_URL_ENV\s*=\s*"([^"]+)"', _HELPER.read_text(), re.M)
    if found is None:
        pytest.fail(
            f"{_HELPER.relative_to(_REPO)} no longer declares MYSQL_URL_ENV — "
            f"this file's whole premise was that it is the canonical name"
        )
    return found.group(1)


def _twins() -> list[Path]:
    return sorted(_TESTS.rglob("*_mysql.py"))


def test_the_helper_still_declares_the_env_var_name():
    """Everything below is measured against this one line, so its absence has to
    be a failure here rather than a silently empty comparison."""
    assert _canonical_env_name()


def test_there_are_dialect_twins_to_check():
    """A glob that matches nothing passes every assertion about its members. If
    the twins are ever renamed out of the `*_mysql.py` shape, this test is the
    thing that notices instead of the suite quietly checking nothing."""
    assert len(_twins()) >= 9, f"expected the nine dialect twins, found {_twins()}"


def test_every_twin_gates_on_the_canonical_env_var_name():
    """Eight of the nine hold their own copy of the name. A copy that drifts does
    not fail — it skips, and a skip is green."""
    canonical = _canonical_env_name()
    offenders: dict[str, list[str]] = {}

    for twin in _twins():
        text = twin.read_text()
        names = set(re.findall(r'MYSQL_URL_ENV\s*=\s*"([^"]+)"', text))
        # A twin that IMPORTS the helper declares nothing of its own, which is
        # the end state we want; it inherits the canonical name by construction.
        #
        # Matched as a real import, not as a substring. A bare
        # `"mysql_dialect" in text` also matches a docstring or a `# see
        # tests/mysql_dialect.py` pointer, so a twin part-way through migration
        # — prose updated, own gate deleted, import not added yet — would be
        # waved through with NO gate at all. This file's own docstring invites
        # exactly that migration, so that state is not hypothetical.
        imports_helper = re.search(
            r"^\s*(?:from\s+tests\.mysql_dialect\s+import|import\s+tests\.mysql_dialect)",
            text,
            re.M,
        ) is not None
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
    set it under a name nothing reads — nine skips, one green job, nobody the
    wiser.

    Parsed as YAML, not grepped. A substring search over the file also matches
    the workflow's own COMMENTS about this variable — of which there are now
    several, one of them a single character away from `NAME:` — so a future
    `# NARRANEXUS_MYSQL_TEST_URL: injected below` would satisfy this assertion
    permanently while the real env line was gone. A guard that prose can satisfy
    is worse than no guard: without one, somebody would still go and look.

    And the question asked is "can the process that runs pytest SEE this
    variable", not "does the name appear in some step's env". Those differ in
    both directions:

    * too wide — step-level `env` reaches only that step, so hanging it on
      `Install dependencies`, or splitting pytest into two steps and env-ing
      only one, keeps this green while nine twins skip in the other;
    * too narrow — lifting the variable to `jobs.backend-tests.env` or the
      workflow's top-level `env` is a legitimate, tidier refactor that would
      turn this red for nothing. False red is how guards get "relaxed" away.

    So: find the steps that actually run pytest, and resolve each one's visible
    environment through Actions' own override order.
    """
    import yaml

    canonical = _canonical_env_name()
    workflow = yaml.safe_load(_WORKFLOW.read_text())

    # Readable failures instead of KeyError, same as _canonical_env_name(). A
    # renamed job (say, split into backend-tests-sqlite / -mysql) should tell the
    # reader what this file was locking, not just which dict key was absent.
    job = (workflow.get("jobs") or {}).get(_CI_JOB)
    if job is None:
        pytest.fail(
            f"{_WORKFLOW.relative_to(_REPO)} no longer has a {_CI_JOB!r} job — "
            f"that is where the MySQL service and {canonical} are wired, so the "
            f"dialect twins have nowhere left to run. Jobs seen: "
            f"{sorted((workflow.get('jobs') or {}).keys())}"
        )
    steps = job.get("steps") or []
    if not steps:
        pytest.fail(f"the {_CI_JOB!r} job has no steps")

    pytest_steps = [s for s in steps if "pytest" in str(s.get("run", ""))]
    assert pytest_steps, (
        f"no step in the {_CI_JOB!r} job runs pytest any more — the job that "
        f"exists to execute the suite (and with it the dialect twins) stopped "
        f"doing so. Steps seen: {[s.get('name') for s in steps]}"
    )

    # Actions' precedence: workflow env < job env < step env.
    workflow_env = workflow.get("env") or {}
    job_env = job.get("env") or {}
    for step in pytest_steps:
        visible = {**workflow_env, **job_env, **(step.get("env") or {})}
        assert canonical in visible, (
            f"the pytest step {step.get('name')!r} cannot see {canonical} — the "
            f"dialect twins would skip in CI, which looks exactly like passing. "
            f"Visible env keys: {sorted(visible)}"
        )
        # The NAME is what this file locks; the URL itself is
        # environment-specific. But an empty value gates the twins off just as
        # effectively as a missing one.
        assert str(visible[canonical]).strip(), (
            f"the pytest step {step.get('name')!r} sets {canonical} to an empty "
            f"value"
        )
