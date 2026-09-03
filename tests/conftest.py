"""
@file_name: conftest.py
@author: Bin Liang
@date: 2026-04-16
@description: Shared pytest fixtures for async DB-backed tests.

Provides `db_client`: a fresh in-memory SQLite-backed AsyncDatabaseClient
per test, with all tables from schema_registry auto-migrated.

Also owns two suite-wide safety nets:

1. `_isolate_shared_db` redirects `settings.database_url` to a per-session
   temp SQLite file, so code paths that lazily acquire the SHARED factory
   client (`db_factory.get_db_client()`, e.g. via
   `XYZBaseModule.get_mcp_db_client()` inside channel/module code under
   test) can never read or write the developer's real database.

2. `pytest_sessionfinish` closes every factory client. This used to be
   justified by the aiosqlite worker being a NON-daemon thread, which made
   a leaked client an interpreter-shutdown hang — the "pytest prints the
   summary but the process never exits" symptom. That worker is a DAEMON
   thread since 2026-08-17 (see `db_backend_sqlite`), so the hang is gone
   and this hook is no longer what keeps the suite exitable. It stays for
   the reason that outlived the hang: a daemon thread is killed wherever
   it stands, so this is the only point at which a leaked client's writes
   are drained and its SQLite locks released deliberately.
"""
import asyncio
import os as _os
import sys

import pytest
import pytest_asyncio

# The telemetry sink defaults to "meta" in production code (shipped
# together with its consent basis — disclosure + settings toggle), but
# the test suite must never phone home REGARDLESS of environment —
# force the kill switch unconditionally, so neither the production
# default nor a developer's `export NEXUS_DIAG_SHIP=full` can leak
# into test processes.
_os.environ["NEXUS_DIAG_SHIP"] = "off"

# The onboarding guide-agent provisioning is scheduled fire-and-forget on
# every login route hit, and defaults ON in production. Force it off for the
# whole suite so unrelated login tests never spawn a real provisioning task
# (agent rows, skill installs) behind their back; the guide-agent tests that
# want it re-enable it explicitly via monkeypatch.
_os.environ["NARRANEXUS_ONBOARDING_GUIDE_AGENT"] = "0"

# The nexus runner warm pool prewarms on EVERY NexusAgent() construction (the
# local/desktop per-turn path), skipping only when there is no running event
# loop. An async test that constructs a NexusAgent DOES have a loop, so the
# skip does not fire and __init__ spawns a real `python -m …nexus_power.runner`
# subprocess (~350 MB RSS) via fire-and-forget create_task — which outlives the
# test as an orphan and binds the pool's asyncio.Lock to a dead loop. Size 0
# disables the pool for the whole suite (no test asserts on it); the subprocess
# STEER-transport tests still exercise their real code path (that branch is
# gated on NEXUS_POWER_INPROCESS, not on the pool). Set at import — before any
# NexusAgent is built — because the pool reads its size once and caches it.
_os.environ["NEXUS_POWER_POOL_SIZE"] = "0"

from xyz_agent_context.utils.db.db_backend_sqlite import SQLiteBackend
from xyz_agent_context.utils.db.database import AsyncDatabaseClient
from xyz_agent_context.utils.db.schema_registry import auto_migrate


@pytest.fixture(scope="session", autouse=True)
def _isolate_shared_db(tmp_path_factory):
    """Point the shared-client factory at a throwaway SQLite file.

    The file is pre-migrated so lazy factory acquisitions see the normal
    schema (empty tables), exactly like a freshly initialized deployment —
    NOT the developer's live database.
    """
    import os

    from xyz_agent_context.settings import settings

    db_path = tmp_path_factory.mktemp("shared_db") / "factory_isolated.db"

    async def _premigrate() -> None:
        backend = SQLiteBackend(str(db_path))
        await backend.initialize()
        await auto_migrate(backend)
        await backend.close()

    asyncio.run(_premigrate())

    original_url = settings.database_url
    original_proxy = os.environ.pop("SQLITE_PROXY_URL", None)
    settings.database_url = f"sqlite:///{db_path}"
    yield
    settings.database_url = original_url
    if original_proxy is not None:
        os.environ["SQLITE_PROXY_URL"] = original_proxy


@pytest.fixture(autouse=True)
def _clear_cwd_owner_cache():
    """The channel-CLI owner cache (data_access.workspace_cwd) is one
    process-wide dict shared by lark and narra — clear it around every
    test or a cached owner leaks ACROSS test modules, which surfaces as
    order-dependent "green alone, red in the full run" failures."""
    from xyz_agent_context.module.data_access.workspace_cwd import _cwd_owner_cache

    _cwd_owner_cache.clear()
    yield
    _cwd_owner_cache.clear()


def pytest_sessionfinish(session, exitstatus):
    """Close leaked factory clients so their worker threads let us exit."""
    from xyz_agent_context.utils.db.db_factory import close_db_client

    asyncio.run(close_db_client())


@pytest_asyncio.fixture
async def db_client():
    """
    In-memory SQLite AsyncDatabaseClient with all tables migrated.
    Each test gets a fresh instance to prevent row leakage across tests.
    """
    backend = SQLiteBackend(":memory:")
    await backend.initialize()
    await auto_migrate(backend)
    client = await AsyncDatabaseClient.create_with_backend(backend)
    yield client
    await client.close()


@pytest.fixture(autouse=True)
def _clear_health_cache():
    """`/health` caches its probe result for a few seconds.

    Autouse and repo-wide: without it one test's outcome answers the next test's
    request. File-local was not enough — `tests/backend/test_health_db_probe.py`
    and `tests/services/test_team_summary_worker.py` both call `main.health()`,
    and whichever ran first left the cache populated for everything after it.

    Only resets a module that is ALREADY imported. The previous version imported
    `backend.main` in every test's setup — cheap after the first one, but it
    made a fixture that most of the suite does not need drag the whole FastAPI
    app into processes that were never going to touch it (`-k` runs, single-file
    runs). `sys.modules` costs a dict lookup and is exact: if nothing imported
    it, nothing cached anything.
    """
    def _reset() -> None:
        module = sys.modules.get("backend.main")
        if module is not None:
            module._health_cache = None

    _reset()
    yield
    _reset()
