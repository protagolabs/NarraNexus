"""
@file_name: test_quota_repository_mysql_underflow.py
@author: Bin Liang
@date: 2026-04-23
@description: Regression tests for the BIGINT UNSIGNED underflow bug in
`atomic_deduct` / `atomic_grant`.

These tests MUST run against a real MySQL backend because the bug only
manifests under UNSIGNED arithmetic — SQLite's signed INTEGER silently
accepts negative intermediates and hides the defect.

Enable by setting `NARRANEXUS_MYSQL_TEST_URL` to a DSN pointing at a
throwaway MySQL. Example for the standard local dev container:

    export NARRANEXUS_MYSQL_TEST_URL=\\
        "mysql://root:xyz_root_pass@127.0.0.1:3306/xyz_agent_context"

Context: in prod (2026-04-22) user xinyao_test_v1 sat with
used_input_tokens = 999_995 of a 1_000_000 cap. Every subsequent
atomic_deduct the service issued failed with:

    (1690, "BIGINT UNSIGNED value is out of range in
     '((initial + granted - used) - delta)'")

because the CASE sub-expression `initial + granted - used - delta`
underflows when `delta > remaining`. MySQL aborts the whole UPDATE, so
`used` never grows past 999_995 and `status` never flips to 'exhausted'.
The effect: the user gets unlimited free-tier NetMind consumption while
the billing stays frozen.

The fix reformulates the comparison additively
(`used + delta >= initial + granted`) so both sides are UNSIGNED sums
that cannot underflow.
"""
from __future__ import annotations

import os

import pytest
import pytest_asyncio

from xyz_agent_context.schema.quota_schema import QuotaStatus
from xyz_agent_context.repository.quota_repository import QuotaRepository
from xyz_agent_context.utils.database import AsyncDatabaseClient
from xyz_agent_context.utils.db_backend_mysql import MySQLBackend
from xyz_agent_context.utils.schema_registry import auto_migrate


MYSQL_URL_ENV = "NARRANEXUS_MYSQL_TEST_URL"


def _parse_mysql_url(url: str) -> dict:
    # Accept `mysql://user:pw@host:port/db`. Kept tiny on purpose —
    # depending on sqlalchemy just to parse a DSN in tests would be overkill.
    assert url.startswith("mysql://"), f"expected mysql://..., got {url!r}"
    body = url[len("mysql://") :]
    creds, _, host_db = body.partition("@")
    user, _, password = creds.partition(":")
    host_port, _, database = host_db.partition("/")
    host, _, port = host_port.partition(":")
    return {
        "host": host,
        "port": int(port) if port else 3306,
        "user": user,
        "password": password,
        "database": database,
    }


pytestmark = pytest.mark.skipif(
    not os.environ.get(MYSQL_URL_ENV),
    reason=(
        f"{MYSQL_URL_ENV} not set. These tests require a real MySQL "
        f"because the UNSIGNED underflow bug does not reproduce on SQLite. "
        f"Example DSN: mysql://root:xyz_root_pass@127.0.0.1:3306/"
        f"xyz_agent_context"
    ),
)


@pytest_asyncio.fixture
async def mysql_repo():
    cfg = _parse_mysql_url(os.environ[MYSQL_URL_ENV])
    backend = MySQLBackend(cfg)
    await backend.initialize()
    await auto_migrate(backend)
    client = await AsyncDatabaseClient.create_with_backend(backend)

    # Each test uses a deterministic user_id and clears that row up-front
    # so reruns are idempotent even though the DB is shared.
    for uid in (
        "usr_underflow_deduct_overshoot",
        "usr_underflow_deduct_at_cap",
        "usr_underflow_grant_insufficient",
    ):
        await client.execute(
            "DELETE FROM user_quotas WHERE user_id = %s",
            params=(uid,),
            fetch=False,
        )

    yield QuotaRepository(client)

    await client.close()


@pytest.mark.asyncio
async def test_atomic_deduct_overshoot_does_not_crash_on_unsigned(mysql_repo):
    """Delta bigger than remaining must succeed: `used` grows past cap and
    status flips to 'exhausted'. Pre-fix this raises MySQL 1690 and the
    whole UPDATE is rolled back — the user then keeps burning free tokens
    forever with no accounting."""
    await mysql_repo.create("usr_underflow_deduct_overshoot", 1_000_000, 1_000_000)
    # Bring used up to the cap boundary minus 5, matching the production
    # xinyao_test_v1 row snapshot.
    await mysql_repo.atomic_deduct("usr_underflow_deduct_overshoot", 999_995, 0)

    # Delta of 100 input would make used = 1_000_095 > cap, which triggers
    # the underflow in the buggy CASE expression.
    await mysql_repo.atomic_deduct("usr_underflow_deduct_overshoot", 100, 0)

    q = await mysql_repo.get_by_user_id("usr_underflow_deduct_overshoot")
    assert q is not None
    assert q.used_input_tokens == 1_000_095, (
        "Deduct must still increment `used` even when it overshoots the cap "
        "— otherwise the row freezes and subsequent check() never flips to "
        "exhausted."
    )
    assert q.status == QuotaStatus.EXHAUSTED


@pytest.mark.asyncio
async def test_atomic_deduct_when_already_at_cap_still_flips_and_increments(
    mysql_repo,
):
    """Second deduct call after `used` already hit the cap must keep
    working. Pre-fix the first overshooting call raises; the row stays
    `active` with `used == cap`; every further call raises again. That's
    the xinyao_test_v1 state observed in prod."""
    await mysql_repo.create("usr_underflow_deduct_at_cap", 1_000, 1_000)
    await mysql_repo.atomic_deduct("usr_underflow_deduct_at_cap", 1_000, 1_000)
    q = await mysql_repo.get_by_user_id("usr_underflow_deduct_at_cap")
    assert q.status == QuotaStatus.EXHAUSTED  # baseline: exact-consume works

    # Now over-consume. Should not raise. Should land past the cap.
    await mysql_repo.atomic_deduct("usr_underflow_deduct_at_cap", 500, 500)
    q = await mysql_repo.get_by_user_id("usr_underflow_deduct_at_cap")
    assert q.used_input_tokens == 1_500
    assert q.used_output_tokens == 1_500
    assert q.status == QuotaStatus.EXHAUSTED


@pytest.mark.asyncio
async def test_atomic_grant_insufficient_to_cover_debt_does_not_crash(mysql_repo):
    """When `used` already exceeds cap and the grant is too small to
    restore a positive balance, the UPDATE must still apply (granted_*
    incremented, status stays 'exhausted'). Pre-fix the CASE sub-
    expression `initial + granted + delta - used` underflows and the
    whole UPDATE rolls back, so the admin's grant disappears silently."""
    await mysql_repo.create("usr_underflow_grant_insufficient", 1_000, 1_000)
    await mysql_repo.atomic_deduct(
        "usr_underflow_grant_insufficient", 5_000, 5_000
    )  # used now 5k/1k on each dimension, exhausted

    # Grant 100 — not enough to cover the 4k overdraw on either side.
    await mysql_repo.atomic_grant("usr_underflow_grant_insufficient", 100, 100)

    q = await mysql_repo.get_by_user_id("usr_underflow_grant_insufficient")
    assert q.granted_input_tokens == 100, "grant must persist even if too small"
    assert q.granted_output_tokens == 100
    assert q.status == QuotaStatus.EXHAUSTED, (
        "insufficient grant must NOT flip status back to active"
    )
