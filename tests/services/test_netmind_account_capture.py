"""
@file_name: test_netmind_account_capture.py
@author:
@date: 2026-07-16
@description: netmind_provisioner._capture_netmind_account stamps the NetMind
account id/email onto ALL of the user's netmind provider rows, best-effort.
"""
import pytest

import xyz_agent_context.services.netmind_provisioner as prov
from xyz_agent_context.services.netmind_auth_client import NetmindUser


async def _seed(db, provider_id, source="netmind"):
    await db.insert("user_providers", {
        "provider_id": provider_id,
        "user_id": "u1",
        "name": provider_id,
        "source": source,
        "protocol": "anthropic",
        "auth_type": "api_key",
    })


@pytest.mark.asyncio
async def test_capture_stamps_all_netmind_rows(db_client, monkeypatch):
    # NetMind onboard can create dual linked rows (anthropic+openai); a
    # non-netmind row must be left untouched.
    await _seed(db_client, "p_anthropic")
    await _seed(db_client, "p_openai")
    await _seed(db_client, "p_other", source="openai")

    async def fake_verify(self, token):
        return NetmindUser(user_system_code="acct_123", email="alice@example.com")
    monkeypatch.setattr(
        "xyz_agent_context.services.netmind_auth_client.NetmindAuthClient.verify_token",
        fake_verify,
    )

    await prov._capture_netmind_account(db_client, "u1", "jwt-token")

    netmind_rows = await db_client.get(
        "user_providers", filters={"user_id": "u1", "source": "netmind"}
    )
    assert len(netmind_rows) == 2
    for r in netmind_rows:
        assert r["netmind_account_id"] == "acct_123"
        assert r["netmind_account_email"] == "alice@example.com"

    other = await db_client.get_one("user_providers", {"provider_id": "p_other"})
    assert not other.get("netmind_account_email")


@pytest.mark.asyncio
async def test_capture_best_effort_on_verify_failure(db_client, monkeypatch):
    await _seed(db_client, "p1")

    async def boom(self, token):
        raise RuntimeError("netmind auth API unreachable")
    monkeypatch.setattr(
        "xyz_agent_context.services.netmind_auth_client.NetmindAuthClient.verify_token",
        boom,
    )

    # Must NOT raise — provisioning already succeeded; capture is best-effort.
    await prov._capture_netmind_account(db_client, "u1", "jwt-token")

    row = await db_client.get_one("user_providers", {"provider_id": "p1"})
    assert not row.get("netmind_account_email")
