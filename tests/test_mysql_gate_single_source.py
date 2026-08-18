"""
@file_name: test_mysql_gate_single_source.py
@author: NarraNexus
@date: 2026-08-17
@description: Every MySQL dialect twin must gate on the same env var name.

`tests/mysql_dialect.py` exists so that "run this against a real MySQL" is
described in one place, and its docstring is honest that it did not finish the
job: at the time of writing exactly one twin imports it and the rest each keep
their own `MYSQL_URL_ENV = "NARRANEXUS_MYSQL_TEST_URL"`.

Copies of a string are fine right up until one of them changes. The failure mode
is the nasty kind: rename the env var in the helper (or in CI) and the copies
stop matching, their `skipif` fires, and **those dialect twins report green while
executing nothing**. That is the exact regression the
`backend-tests` CI job was added to prevent, arriving through the one door the
job cannot see — a skip is not a failure.

So until the copies are migrated, the invariant is enforced from outside: every
`*_mysql.py` twin's gate names the same variable, and the CI workflow passes that
same name to the step that runs the suite.
This test needs no MySQL and no container; it reads files.

Whoever migrates the copies into `tests.mysql_dialect` should keep this test —
it then guards the helper against CI drifting away from it, which is the half
that survives the refactor.
"""
from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[1]
_TESTS = _REPO / "tests"
_WORKFLOW = _REPO / ".github/workflows/ci.yml"
_CI_JOB = "backend-tests"

# Written down, not derived from the glob: deriving it from `_twins()` would make
# the assertion self-satisfying. Lowering it is the job of whichever commit
# removes a twin.
# 10 as of 2026-08-18: `test_team_posting_mysql.py` was added with the harness
# redesign, covering the hop cap's variable-placeholder NOT IN, the DM lookup's
# three-way join and the wake signal's update-then-insert.
#
# Raising this is the obligation of whoever adds a twin, and dev's version of this
# guard now asserts equality rather than a floor — measured there: with the floor
# at 9 and ten twins present, renaming one out of `*_mysql.py` still passed, and
# that twin became invisible to the env-name check and free to drift and skip in
# CI indefinitely. A floor is only pressured downward; `F == N` is what makes an
# addition visible.
_TWIN_FLOOR = 10

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
            f"check the dialect twins against"
        )
    found = re.search(r'^MYSQL_URL_ENV\s*=\s*"([^"]+)"', _HELPER.read_text(), re.M)
    if found is None:
        pytest.fail(
            f"{_HELPER.relative_to(_REPO)} no longer declares MYSQL_URL_ENV — "
            f"this file's whole premise was that it is the canonical name"
        )
    return found.group(1)


def _imports_the_helper(path: Path, text: str) -> bool:
    """True iff this module really imports `mysql_dialect`, in any spelling."""
    try:
        tree = ast.parse(text, filename=str(path))
    except SyntaxError as exc:  # pragma: no cover — a broken twin
        pytest.fail(
            f"{path.relative_to(_REPO)} does not parse ({exc}), so whether it "
            f"gates on the canonical env var cannot be established"
        )
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            if (node.module or "").endswith("mysql_dialect"):
                return True
            if any(a.name == "mysql_dialect" for a in node.names):
                return True
        elif isinstance(node, ast.Import):
            if any(a.name.endswith("mysql_dialect") for a in node.names):
                return True
    return False


def _twins() -> list[Path]:
    return sorted(_TESTS.rglob("*_mysql.py"))


def test_the_helper_still_declares_the_env_var_name():
    """Everything below is measured against this one line, so its absence has to
    be a failure here rather than a silently empty comparison."""
    assert _canonical_env_name()


def test_the_twins_have_not_quietly_left_the_check():
    """A glob that matches nothing passes every assertion about its members — and
    a glob that matches SIX when there were nine passes them just as quietly.

    `_TWIN_FLOOR` was briefly replaced by "the list is non-empty", on the stated
    grounds that a number would go stale when a tenth twin arrives. That reason
    is false and worth recording: `>=` is a floor, so a tenth twin keeps this
    green; only the failure *message* would have needed rewording. What the
    weaker assertion cost was the half that matters — rename three twins out of
    the `*_mysql.py` shape and `test_every_twin_gates_on_the_canonical_env_var_name`
    silently checks six files, while the three that escaped can drift their env
    name and skip in CI, green, forever.

    So: a floor. It fires only when twins LEAVE the shape (renamed, or deleted
    with their feature), which is a deliberate act — lowering the number belongs
    in the same commit as the removal, with the reason.
    """
    found = _twins()
    assert len(found) >= _TWIN_FLOOR, (
        f"expected at least {_TWIN_FLOOR} `*_mysql.py` dialect twins, found "
        f"{[str(p.relative_to(_REPO)) for p in found]} — either some were renamed "
        f"out of that shape (silently leaving the canonical-env check below), or "
        f"one was deleted. If the removal was intended, lower _TWIN_FLOOR here "
        f"and say why."
    )


def test_every_twin_gates_on_the_canonical_env_var_name():
    """Most of them hold their own copy of the name. A copy that drifts does not
    fail — it skips, and a skip is green."""
    canonical = _canonical_env_name()
    offenders: dict[str, list[str]] = {}

    for twin in _twins():
        text = twin.read_text()
        names = set(re.findall(r'MYSQL_URL_ENV\s*=\s*"([^"]+)"', text))
        # A twin that IMPORTS the helper declares nothing of its own, which is
        # the end state we want; it inherits the canonical name by construction.
        #
        # Asked of the parsed imports, not of the text. Enumerating spellings
        # got this wrong twice in a row: a bare `"mysql_dialect" in text` let a
        # docstring mention grant amnesty, and the regex that replaced it used
        # `[^\n]*`, which does not exclude `#` — so
        # `from tests import conftest  # gate lives in mysql_dialect.py` also
        # counted, for a twin with no gate at all. It also missed the
        # parenthesised form. The AST knows all three and cannot be satisfied by
        # a comment.
        imports_helper = _imports_the_helper(twin, text)
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

    # `make test` is this repo's other way of running the suite (its target is
    # `uv run pytest tests/ -v`), so matching only "pytest" would false-red on a
    # perfectly good rewrite of the step.
    pytest_steps = [
        s for s in steps
        if re.search(r"pytest|make\s+test", str(s.get("run", "")))
    ]
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
