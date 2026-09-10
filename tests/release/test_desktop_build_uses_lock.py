"""
@file_name: test_desktop_build_uses_lock.py
@author: NarraNexus
@date: 2026-09-10
@description: The DMG's Python install must come from uv.lock, and the build
              must import what it packaged before shipping it.

2026-09-10 incident. Step 3 of build-desktop.sh was `uv pip install
"$PROJECT_ROOT"`, which RE-RESOLVES every dependency from the ranges in
pyproject.toml and ignores uv.lock. The dmg therefore shipped whatever PyPI
held on build day: 99 of the 176 resolved packages differed from the lock, and
one of them — `mcp` 2.2.0 against a locked 1.24.0 — had renamed
`streamablehttp_client` to `streamable_http_client`. Every sidecar service in
the shipped app died at import; the user saw "SQLite Proxy failed to start".

Two things had to be true for that to reach a user, and this file guards both:

  1. the install path bypassed the lock            → `test_step3_*`
  2. the build never imported the code it packaged → `test_import_smoke_*`

Same "mirror a fact + assert they agree" shape as test_plugins_extra_lockstep.py
(which guards a different property of the same step: that it stays uv-driven and
light). Cheap, and both fail on a plain `git revert` of the fix.
"""
from __future__ import annotations

import ast
import re
from pathlib import Path

from tests._shell_commands import command_lines

REPO = Path(__file__).resolve().parents[2]
SCRIPT = REPO / "scripts" / "release" / "build-desktop.sh"
SMOKE = REPO / "scripts" / "release" / "bundle_import_smoke.py"
STATE_RS = REPO / "tauri" / "src-tauri" / "src" / "state.rs"


def test_step3_exports_the_lock() -> None:
    exports = command_lines(SCRIPT, "uv export")
    assert exports, (
        "build-desktop.sh no longer exports uv.lock — the bundled interpreter is "
        "then resolved from the pyproject ranges, which is the 2026-09-10 mcp-2.x "
        "breakage verbatim"
    )
    for line in exports:
        # `--locked`, not `--frozen`: uv's `--frozen` means "don't update the
        # lock before exporting", which exports a lock that is out of date with
        # pyproject.toml WITHOUT complaining. `--locked` asserts the lock would
        # not change and exits non-zero when it would, which is the property
        # this step needs — a forgotten `uv lock` must not ship a bundle that
        # is missing a dependency someone added.
        assert "--locked" in line, (
            "`uv export` must use --locked; with --frozen (or neither) a lock that "
            f"is out of date with pyproject.toml is used silently:\n    {line}"
        )
        assert "--no-editable" in line, (
            "the bundled install must stay NON-editable — an editable install bakes "
            f"the build machine's absolute source path into the .app:\n    {line}"
        )
        # The export decides what ends up in the bundle, so the light-build
        # invariant is asserted HERE, on the line that carries it.
        assert "--no-dev" in line, (
            "the export must exclude the dev group; without it pytest / ruff / "
            f"pyright / pillow all ship inside the .app:\n    {line}"
        )
        assert "--extra plugins" not in line and "--all-extras" not in line, (
            "the desktop build stays the LIGHT one — the coding-agent SDKs "
            "(~186 MB claude-agent-sdk) are installed on demand from Settings → "
            f"Plugins, not exported into the bundle:\n    {line}"
        )


def test_step3_installs_from_the_exported_requirements() -> None:
    installs = command_lines(SCRIPT, "uv pip install")
    assert installs, "build-desktop.sh no longer installs the project — update this guard"
    for line in installs:
        assert re.search(r"(^|\s)(-r\s|--requirements?[=\s])", line), (
            "the bundled install must read the exported requirements file; installing "
            f"the project directly re-resolves from the pyproject ranges:\n    {line}"
        )
        # The exact 2026-09-10 shape, spelled out so a revert cannot pass by
        # keeping a `-r` somewhere else on the same line.
        assert not re.search(r'uv pip install[^|;]*\s"\$PROJECT_ROOT"', line), (
            "installing $PROJECT_ROOT directly is the pre-2026-09-10 command that "
            f"ignored uv.lock:\n    {line}"
        )


def test_import_smoke_runs_in_the_build() -> None:
    assert SMOKE.is_file(), f"missing {SMOKE.relative_to(REPO)}"
    calls = command_lines(SCRIPT, "bundle_import_smoke.py")
    assert calls, (
        "build-desktop.sh no longer runs the bundle import smoke test — an "
        "unimportable bundle would again be signed, notarized and shipped"
    )
    assert any("$PYTHON_DIR" in line for line in calls), (
        "the smoke test must run under the BUNDLED interpreter (the whole point is "
        "to exercise the wheels just installed there):\n    " + "\n    ".join(calls)
    )


def _smoke_tuple(name: str) -> tuple[str, ...]:
    """A module-level tuple constant out of the smoke script, read via AST.

    Reading rather than importing: this file must not execute the smoke script,
    which imports the whole backend. `AnnAssign` is matched too — an annotated
    `ENTRYPOINTS: tuple[str, ...] = (...)` is the natural thing to write in a
    repo that runs pyright, and it would otherwise read as "constant missing".
    """
    for node in ast.parse(SMOKE.read_text(encoding="utf-8")).body:
        targets = (
            node.targets if isinstance(node, ast.Assign)
            else [node.target] if isinstance(node, ast.AnnAssign)
            else []
        )
        if not any(isinstance(t, ast.Name) and t.id == name for t in targets):
            continue
        # A bare annotation (`ENTRYPOINTS: tuple[str, ...]`) has no value. Say
        # that, rather than "not found" — the two need different fixes.
        assert node.value is not None, (
            f"{name} is declared in bundle_import_smoke.py but has no value"
        )
        return tuple(ast.literal_eval(node.value))
    raise AssertionError(f"{name} not found in bundle_import_smoke.py")


def _smoke_entrypoints() -> set[str]:
    """ENTRYPOINTS out of the smoke script, read without importing it."""
    return set(_smoke_tuple("ENTRYPOINTS"))


def _args_blocks(body: str) -> list[str]:
    """Every `args: vec![ ... ]` body, sliced by BRACKET MATCHING.

    A regex that stops at the first `],` is at the mercy of Rust formatting: a
    trailing `// comment` after the closing bracket, or a single-line `vec![..]`,
    makes the whole ServiceDef invisible — and an invisible ServiceDef is
    exactly the case this guard exists to catch, failing OPEN. Counting
    brackets has no such blind spot.
    """
    blocks: list[str] = []
    marker = "args: vec!["
    idx = body.find(marker)
    while idx != -1:
        start = idx + len(marker)
        depth, i = 1, start
        while i < len(body) and depth:
            if body[i] == "[":
                depth += 1
            elif body[i] == "]":
                depth -= 1
            i += 1
        blocks.append(body[start:i - 1])
        idx = body.find(marker, i)
    return blocks


def _state_rs_entrypoints() -> set[str]:
    """The modules `bundled_services()` in state.rs actually launches.

    Reads the Rust source rather than a hand-copied list: the point of the test
    is that a service added there and not to the smoke script is a service whose
    dependency graph the build never checks.
    """
    text = STATE_RS.read_text(encoding="utf-8")
    start = text.index("fn bundled_services")
    # bundled_services only — dev_services runs the same modules through
    # `uv run` against the source tree, which is not what the dmg ships.
    end = text.index("fn dev_services", start)
    body = text[start:end]

    blocks = _args_blocks(body)
    # Structural invariant: one args vec per ServiceDef. If a future edit makes
    # a ServiceDef unparseable, this is red — never a silently smaller set that
    # still equals ENTRYPOINTS.
    assert len(blocks) == body.count("ServiceDef {"), (
        f"parsed {len(blocks)} `args: vec![..]` blocks but state.rs "
        f"bundled_services() declares {body.count('ServiceDef {')} ServiceDef(s) — "
        f"the parser lost one; fix it rather than the count"
    )

    found: set[str] = set()
    for block in blocks:
        literals = re.findall(r'"([^"]*)"', block)
        # `-m uvicorn backend.main:app`: what gets imported is the `mod:app`,
        # not the runner `-m` names. Keyed on the runner actually being there,
        # so an unrelated `foo:bar`-shaped literal cannot swallow the `-m`.
        runs_asgi_server = "uvicorn" in literals
        from_block: set[str] = set()
        for i, literal in enumerate(literals):
            # By POSITION, never by package prefix: a prefix whitelist silently
            # ignores a future service under a different top-level package, and
            # the whole point of this guard is that a NEW service cannot slip in
            # unimported.
            if literal == "-m" and i + 1 < len(literals):
                # The runner counts too, and is ADDED rather than replaced by
                # the mod:app below: a `-m <runner> <target>` service whose
                # runner is silently dropped is a piece of the bundle nobody
                # imports, which is the exact hole this guard exists to close.
                from_block.add(literals[i + 1])
            elif literal.endswith(".py"):
                # launched by path (module_runner.py) — same import graph
                from_block.add(literal.removeprefix("src/").removesuffix(".py").replace("/", "."))
            elif runs_asgi_server and re.fullmatch(r"[A-Za-z_][\w.]*:[A-Za-z_]\w*", literal):
                # uvicorn's "backend.main:app"
                from_block.add(literal.split(":")[0])
        assert from_block, (
            "a bundled_services() entry yielded no importable module — the launch "
            f"shape is new (console script? bare path?) and this parser has to learn "
            f"it, otherwise that service ships unverified:\n    {literals}"
        )
        found |= from_block
    return found


def test_smoke_entrypoints_match_state_rs() -> None:
    smoke, rust = _smoke_entrypoints(), _state_rs_entrypoints()
    assert smoke == rust, (
        "bundle_import_smoke.ENTRYPOINTS is out of lockstep with state.rs "
        f"bundled_services():\n  only in state.rs: {sorted(rust - smoke)}\n"
        f"  only in the smoke test: {sorted(smoke - rust)}"
    )


def test_uv_is_pinned_and_consistent_across_workflows() -> None:
    """Every setup-uv step pins uv, and they all pin the SAME uv.

    `uv export --locked` asserts the lock would not change under the uv that
    runs it, so which uv CI installs is part of the build contract. Drift
    between the two workflows separates a green PR from a red release: ci.yml
    proves the lock against one uv while build-desktop.yml, which only runs
    after the tag is pushed, proves it against another. Asserted by content,
    not by count — a hardcoded number of steps rots the moment a job is added.
    """
    import yaml  # pyyaml is a runtime dependency (pyproject.toml)

    # The whole workflow directory, not a hardcoded list: a file list rots for
    # the same reason a step count does — a new workflow that installs uv would
    # simply be invisible to a guard that only knows today's two.
    pins: list[tuple[str, str]] = []
    unpinned: list[str] = []
    for path in sorted((REPO / ".github/workflows").glob("*.y*ml")):
        rel = path.relative_to(REPO)
        workflow = yaml.safe_load(path.read_text(encoding="utf-8"))
        for job_name, job in (workflow.get("jobs") or {}).items():
            for index, step in enumerate(job.get("steps") or []):
                if "astral-sh/setup-uv" not in str(step.get("uses", "")):
                    continue
                version = (step.get("with") or {}).get("version")
                # Keyed by step, not by job: two setup-uv steps in one job would
                # otherwise collapse into one and hide a disagreement.
                where = f"{rel}:{job_name}:step{index}"
                if version:
                    pins.append((where, str(version)))
                else:
                    unpinned.append(where)

    # More important now that the file list is a glob: a mistyped pattern would
    # otherwise scan nothing and pass.
    assert pins, "no setup-uv steps found — did the workflows move? update this guard"
    assert not unpinned, (
        "these setup-uv steps install whatever uv is newest that day, which "
        "`uv export --locked` turns into a failure after the tag is pushed:\n  "
        + "\n  ".join(unpinned)
    )
    assert len({version for _, version in pins}) == 1, (
        f"the workflows pin different uv versions, so CI and the release build "
        f"prove the lock against different resolvers: {pins}"
    )


def test_smoke_checks_uvicorns_lazily_resolved_deps() -> None:
    """`websockets` must stay in the smoke test, and the loop must still run it.

    uvicorn stores its protocol/loop implementations as import-path STRINGS and
    resolves them in `Config.load()`, so `import uvicorn` leaves uvloop,
    httptools and websockets untouched. `websockets` is the one that bites:
    it is a direct dependency of this project that NOTHING in src/ backend/
    plugins/ packages/ imports, so uvicorn's websockets_impl is its only
    consumer — and a broken wheel there does not raise, it just logs
    "Unsupported upgrade request" per connection while the build stays green and
    the desktop app's chat WS is dead.

    Asserted by name and by structure, never by a total count: this file has
    already paid for a hardcoded "four entrypoints" once.
    """
    lazy = _smoke_tuple("LAZY_RUNTIME_IMPORTS")
    assert "websockets" in lazy, (
        "websockets dropped out of the smoke test — nothing else in the repo "
        f"imports it, so the bundle would ship it unverified: {lazy}"
    )

    # ...and ONE loop must iterate both tuples. Asserting only that each name
    # appears in some loop would still pass if the lazy group were split into
    # its own pass that merely warns — which is the most natural way to weaken
    # this (uvloop failing really is just a perf regression, so "that one
    # shouldn't be fatal" is an easy sell) and exactly what the script's own
    # header argues against.
    per_loop: list[set[str]] = []
    for node in ast.walk(ast.parse(SMOKE.read_text(encoding="utf-8"))):
        if isinstance(node, ast.For):
            per_loop.append({sub.id for sub in ast.walk(node.iter) if isinstance(sub, ast.Name)})
    assert any({"ENTRYPOINTS", "LAZY_RUNTIME_IMPORTS"} <= names for names in per_loop), (
        "no single loop iterates both ENTRYPOINTS and LAZY_RUNTIME_IMPORTS — the "
        "two must share one fatal path, or the lazy group can be quietly demoted "
        f"to a warning (loops found: {[sorted(n) for n in per_loop if n]})"
    )
