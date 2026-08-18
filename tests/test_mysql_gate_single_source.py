"""
@file_name: test_mysql_gate_single_source.py
@author: NarraNexus
@date: 2026-08-17
@description: Every MySQL dialect twin must gate on the same env var name.

`tests/mysql_dialect.py` exists so that "run this against a real MySQL" is
described in one place, and its docstring is honest that it did not finish the
job: one twin imports it (`tests/backend/test_team_room_activity_mysql.py`)
and the rest each keep their own `MYSQL_URL_ENV = "NARRANEXUS_MYSQL_TEST_URL"`.

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
# the assertion self-satisfying.
#
# **A floor is only tight while it equals the actual count.** It is pressured
# downward only — with 10 twins and a floor of 9, one twin can be renamed out of
# the `*_mysql.py` shape and this stays green, which is precisely the silent
# departure it exists to catch (measured: 10 twins, rename one, 4 passed).
# So both directions are obligations of the commit that changes the set:
#   * ADDING a twin must raise this number, or the guard goes slack by one;
#   * REMOVING one must lower it, with the reason.
# `test_the_twins_have_not_quietly_left_the_check` enforces the first half by
# also failing when the count runs AHEAD of the floor.
_TWIN_FLOOR = 9

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


def _parse(path: Path, text: str) -> ast.AST:
    try:
        return ast.parse(text, filename=str(path))
    except SyntaxError as exc:  # pragma: no cover — a broken twin
        pytest.fail(
            f"{path.relative_to(_REPO)} does not parse ({exc}), so whether it "
            f"gates on the canonical env var cannot be established"
        )


def _imports_the_helper(path: Path, text: str) -> bool:
    """True iff this module really imports `mysql_dialect`, in any spelling."""
    # Compare the last dotted SEGMENT, not a string suffix: `endswith` would also
    # accept `tests.legacy_mysql_dialect`, and a stray match here grants amnesty
    # to a twin with no gate at all.
    #
    # Splitting is strictly load-bearing only on the `ast.Import` branch, the one
    # place `alias.name` can be dotted (`import tests.mysql_dialect`). On
    # `ImportFrom`, `node.module` carries the module path — `None` for
    # `from . import mysql_dialect` (hence `or ""`, which prevents a TypeError),
    # and the bare name for `from ..mysql_dialect import X`, since the level
    # lives in `node.level`. The `alias.name` comparison is exact because that
    # branch sees an already-single name; aliases cannot slip past it either way,
    # `alias.name` is the real name and `asname` holds the alias.
    for node in ast.walk(_parse(path, text)):
        if isinstance(node, ast.ImportFrom):
            if (node.module or "").split(".")[-1] == "mysql_dialect":
                return True
            if any(a.name == "mysql_dialect" for a in node.names):
                return True
        elif isinstance(node, ast.Import):
            if any(a.name.split(".")[-1] == "mysql_dialect" for a in node.names):
                return True
    return False


def _declared_env_names(path: Path, text: str) -> set[str]:
    """Every string assigned to `MYSQL_URL_ENV`, from the AST.

    Read from the syntax tree rather than by `re.findall` over the file, because
    a text scan accepts and rejects for the wrong reasons — measured, both
    directions:

      * a twin with NO gate at all, whose only trace is
        `# gate removed; used to be MYSQL_URL_ENV = "NARRANEXUS_MYSQL_TEST_URL"`,
        passed — the precise false green this file exists to stop;
      * a correctly gated twin whose docstring narrates
        `Historically this was MYSQL_URL_ENV = "OLD_MYSQL_URL"` failed, and a
        false red on a correct twin is how a guard gets "relaxed" away.

    Both are live: the twins' docstrings routinely quote the env var, one of them
    verbatim as an `export` line.
    """
    found: set[str] = set()
    for node in ast.walk(_parse(path, text)):
        targets = (
            node.targets if isinstance(node, ast.Assign)
            else [node.target] if isinstance(node, ast.AnnAssign)
            else []
        )
        if not any(isinstance(t, ast.Name) and t.id == "MYSQL_URL_ENV" for t in targets):
            continue
        value = node.value
        if isinstance(value, ast.Constant) and isinstance(value.value, str):
            found.add(value.value)
    return found


def _has_skip_gate(path: Path, text: str) -> bool:
    """True iff the module actually installs a skip gate.

    Matching the env var's NAME never established that anything acts on it: a
    twin holding the right string with no `skipif` would run unconditionally and
    error on every machine without MySQL. Every twin today gates with a
    module-level `pytestmark`; a per-test `@pytest.mark.skipif` counts too.
    """
    tree = _parse(path, text)
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and any(
            isinstance(t, ast.Name) and t.id == "pytestmark" for t in node.targets
        ):
            return True
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if any("skipif" in ast.dump(d) for d in node.decorator_list):
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
    a glob that matches fewer files than there are twins passes them just as
    quietly: the twins that escaped the shape stop being checked, and can then
    drift their env name and skip in CI, green, forever.

    Checked in both directions, because a floor below the real count is slack
    rather than a guard: fewer than the floor means a twin left the shape, more
    than the floor means one was added without tightening it and the next
    departure will be silent.
    """
    found = _twins()
    assert len(found) >= _TWIN_FLOOR, (
        f"expected at least {_TWIN_FLOOR} `*_mysql.py` dialect twins, found "
        f"{[str(p.relative_to(_REPO)) for p in found]} — either some were renamed "
        f"out of that shape (silently leaving the canonical-env check below), or "
        f"one was deleted. If the removal was intended, lower _TWIN_FLOOR here "
        f"and say why."
    )
    assert len(found) == _TWIN_FLOOR, (
        f"found {len(found)} dialect twins but _TWIN_FLOOR is {_TWIN_FLOOR} — a "
        f"twin was added without raising it, which leaves the floor slack by "
        f"{len(found) - _TWIN_FLOOR} and lets that many twins later leave the "
        f"`*_mysql.py` shape without this test noticing. Raise _TWIN_FLOOR to "
        f"{len(found)}."
    )


def test_every_twin_gates_on_the_canonical_env_var_name():
    """Most twins hold their own copy of the name. A copy that drifts does not
    fail — it skips, and a skip is green. So does a twin that holds the right
    name and never acts on it, which is why the gate's existence is asserted too.
    """
    canonical = _canonical_env_name()
    offenders: dict[str, list[str]] = {}

    for twin in _twins():
        text = twin.read_text()
        names = _declared_env_names(twin, text)
        # A twin that IMPORTS the helper declares nothing of its own; it inherits
        # the canonical name by construction.
        imports_helper = _imports_the_helper(twin, text)

        problems = sorted(n for n in names if n != canonical)
        if not names and not imports_helper:
            problems = ["<no gate at all>"]
        elif not _has_skip_gate(twin, text):
            problems = problems or ["<names the env var but installs no skipif>"]
        if problems:
            offenders[str(twin.relative_to(_REPO))] = problems

    assert not offenders, (
        f"these dialect twins do not gate on {canonical!r}, so they will skip "
        f"(and report green) wherever it is configured: {offenders}"
    )


def test_ci_passes_the_same_env_var_name_the_twins_read():
    """The other end of the same wire. CI can be right about the URL and still
    set it under a name nothing reads — every twin skips, the job stays green,
    nobody the wiser.

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
      only one, keeps this green while the twins skip in the other;
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
