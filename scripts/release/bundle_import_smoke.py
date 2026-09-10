"""
@file_name: bundle_import_smoke.py
@author: NarraNexus
@date: 2026-09-10
@description: Import smoke test for a freshly populated interpreter — imports
              every sidecar entrypoint the desktop app launches and fails on
              ANY exception. Run by scripts/release/build-desktop.sh (step 3.1)
              with the BUNDLED interpreter, right after the dependencies land.

Assumes a POSIX bundle, because that is the only bundle we build (macOS dmg).
It matters for one name: `uvloop` is a conditional dependency in uv.lock
(`sys_platform != 'win32' and != 'cygwin'`, non-PyPy), so on a Windows bundle it
would be correctly absent and this script would fail on it. Whoever builds that
bundle should narrow the list by platform — NOT swallow the failure, which would
quietly drop httptools and websockets from the check as well.

Usage:
    <bundled python> scripts/release/bundle_import_smoke.py
"""
from __future__ import annotations

import importlib
import os
import sys
import tempfile
import traceback

# The four processes tauri/src-tauri/src/state.rs spawns in a packaged .app
# (`bundled_services()`), as importable module paths. `module_runner` is
# launched there by file path rather than `-m`; importing it as a module
# exercises the same import graph. Keep this list in lockstep with state.rs —
# a service added there and not here is a service whose dependency graph the
# build never checks (guarded by
# tests/release/test_desktop_build_uses_lock.py::test_smoke_entrypoints_match_state_rs).
ENTRYPOINTS = (
    "narranexus.platform.utils.db.sqlite_proxy_server",
    "narranexus.platform.module_system.module_runner",
    "narranexus.platform.module_system.run_worker_supervisor",
    # backend is launched as `-m uvicorn backend.main:app`, so BOTH halves have
    # to import: `backend/main.py` never imports uvicorn itself, which would
    # have left the server as the one piece of the bundle nothing checked.
    "uvicorn",
    "backend.main",
)

# `import uvicorn` does NOT reach these. uvicorn keeps its protocol/loop
# implementations as import-path STRINGS and resolves them through
# `import_from_string` inside `Config.load()`, so after importing uvicorn none
# of uvloop / httptools / websockets is in sys.modules (verified 2026-09-10).
# That matters most for `websockets`: it is a direct dependency of this project
# and NOTHING in src/ backend/ plugins/ packages/ imports it — uvicorn's
# websockets_impl is its only consumer. A broken wheel there does not raise; the
# server just logs "Unsupported upgrade request" per connection and the desktop
# app's chat WS is dead, with a green build behind it.
#
# Kept out of ENTRYPOINTS on purpose: that tuple is asserted equal to what
# state.rs launches (test_desktop_build_uses_lock.py), and these are libraries,
# not entrypoints.
LAZY_RUNTIME_IMPORTS = (
    "uvloop",
    "httptools",
    "websockets",
)


def _isolate_environment(scratch: str) -> None:
    """Point everything stateful at a scratch dir before the first import.

    `backend.main` builds its FastAPI app and loads the builtin plugins AT
    IMPORT TIME, so it reads a home directory, a plugin tree and a database
    URL. Left alone it would read the build machine's real ones — a green or
    red result that depends on who is building. Pinning them to a throwaway
    directory makes this gate deterministic, and is what lets it treat every
    exception as fatal instead of waving config errors through.
    """
    os.environ["HOME"] = scratch
    os.environ["NARRANEXUS_PLUGIN_HOME"] = os.path.join(scratch, "plugins")
    os.environ["DATABASE_URL"] = f"sqlite:///{os.path.join(scratch, 'smoke.db')}"
    # "desktop" is what process_manager.rs sets for these very processes;
    # importing under the surface the dmg actually runs is the point.
    os.environ["NARRA_SURFACE"] = "desktop"


def main() -> int:
    failures: list[str] = []
    with tempfile.TemporaryDirectory(prefix="nn-import-smoke-") as scratch:
        _isolate_environment(scratch)
        for module in ENTRYPOINTS + LAZY_RUNTIME_IMPORTS:
            try:
                importlib.import_module(module)
            except Exception as exc:  # noqa: BLE001 - see below
                # EVERY exception is fatal, not just ImportError. A dependency
                # that jumped a major usually does NOT announce itself as an
                # ImportError: starlette/fastapi surface as TypeError (changed
                # signature), a removed attribute as AttributeError, pydantic
                # as PydanticUserError, and a builtin plugin that fails to load
                # is re-raised by kernel.plugins.loader. Of the four packages
                # that actually drifted into the 2026-09-10 dmg, only mcp
                # happened to fail with an ImportError — an ImportError-only
                # gate would have caught one case in four.
                failures.append(f"{module}: {type(exc).__name__}: {exc}")
                traceback.print_exc()
            else:
                print(f"  ✓ {module}")

    if failures:
        print("\nBundle import smoke test FAILED:", file=sys.stderr)
        for failure in failures:
            print(f"  ✗ {failure}", file=sys.stderr)
        print(
            "\nThe interpreter in this bundle cannot import the app. Almost always a "
            "dependency-version mismatch: the install path must go through uv.lock "
            "(`uv export --locked` -> `uv pip install -r`), never the pyproject ranges.",
            file=sys.stderr,
        )
        return 1

    checked = len(ENTRYPOINTS) + len(LAZY_RUNTIME_IMPORTS)
    print(f"All {checked} bundle imports clean "
          f"({len(ENTRYPOINTS)} sidecar entrypoints + {len(LAZY_RUNTIME_IMPORTS)} lazily-resolved runtime deps).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
