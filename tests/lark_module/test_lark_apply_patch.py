"""
@file_name: test_lark_apply_patch.py
@date: 2026-08-11
@description: LarkCredentialManager.apply_patch — the write primitive behind the
seam's patch_credential (blueprint P2 lark write leg). Proves the read →
to_raw_dict → deep_merge → _cred_from_raw → save round-trip: a permission_state
step MERGES into the existing blob (not replace), and a top-level field (e.g.
app_secret_encoded) is set — exactly what the old patch_permission_state /
set_app_secret_encoded did, now via one generic path.
"""
from __future__ import annotations

import asyncio
import json

import pytest

from xyz_agent_context.module.lark_module._lark_credential_manager import (
    LarkCredentialManager,
    _encode_secret,
)


class _FakeDb:
    """A one-row lark_credentials world; captures the update payload."""

    def __init__(self, row):
        self.row = row
        self.updated = None

    async def get_one(self, table, filters):
        return dict(self.row) if filters.get("agent_id") == self.row["agent_id"] else None

    async def update(self, table, filters, data):
        self.updated = data

    async def insert(self, table, data):  # pragma: no cover - existing row path
        self.updated = data


def _row(**over):
    base = {
        "agent_id": "agent_x", "app_id": "cli_x", "app_secret_ref": "ref",
        "app_secret_encrypted": _encode_secret("s3cret"), "brand": "feishu",
        "profile_name": "agent_agent_x", "workspace_path": "/ws", "bot_name": "Bot",
        "bot_open_id": "ou_b", "owner_open_id": "ou_o", "owner_name": "Al",
        "auth_status": "bot_ready", "is_active": 1,
        "permission_state": json.dumps({"admin_request_url": "u1", "admin_approved_at": "t0"}),
    }
    base.update(over)
    return base


def test_apply_patch_merges_permission_state_into_the_existing_blob():
    db = _FakeDb(_row())
    mgr = LarkCredentialManager(db)
    asyncio.run(mgr.apply_patch("agent_x", {"permission_state": {"user_authz_url": "u3"}}))
    saved = json.loads(db.updated["permission_state"])
    # existing keys preserved, new key added — a MERGE, not a replace
    assert saved == {"admin_request_url": "u1", "admin_approved_at": "t0", "user_authz_url": "u3"}


def test_apply_patch_sets_a_top_level_field():
    db = _FakeDb(_row())
    mgr = LarkCredentialManager(db)
    new_secret_encoded = _encode_secret("new-secret")
    asyncio.run(mgr.apply_patch("agent_x", {"app_secret_encoded": new_secret_encoded}))
    assert db.updated["app_secret_encrypted"] == new_secret_encoded
    # permission_state is not even WRITTEN by a non-permission patch (column-
    # targeted): the whole point is not to clobber a disjoint column.
    assert "permission_state" not in db.updated


def test_apply_patch_writes_only_the_patched_columns():
    # The concurrency fix: apply_patch must write ONLY the columns its patch
    # names, so a concurrent single-column writer (update_auth_status /
    # update_workspace_path) on a DISJOINT column isn't clobbered by a whole-row
    # rewrite of a stale read.
    db = _FakeDb(_row())
    mgr = LarkCredentialManager(db)
    asyncio.run(mgr.apply_patch("agent_x", {"permission_state": {"user_authz_url": "u3"}}))
    assert set(db.updated) == {"permission_state"}, db.updated

    db2 = _FakeDb(_row())
    mgr2 = LarkCredentialManager(db2)
    asyncio.run(mgr2.apply_patch("agent_x", {"app_id": "cli_new", "is_active": False}))
    # app_id + is_active only — NOT auth_status / workspace_path / bot_* etc.
    assert set(db2.updated) == {"app_id", "is_active"}, db2.updated
    assert db2.updated["is_active"] == 0  # bool serialized to 0/1


def test_apply_patch_rejects_unknown_field():
    db = _FakeDb(_row())
    mgr = LarkCredentialManager(db)
    with pytest.raises(ValueError, match="unknown lark credential field"):
        asyncio.run(mgr.apply_patch("agent_x", {"not_a_real_column": "x"}))


def test_apply_patch_on_missing_credential_raises_clearly():
    db = _FakeDb(_row())
    mgr = LarkCredentialManager(db)
    with pytest.raises(ValueError, match="no Lark credential"):
        asyncio.run(mgr.apply_patch("nobody", {"auth_status": "x"}))


def test_save_raw_pins_agent_id_from_the_path():
    db = _FakeDb(_row())
    mgr = LarkCredentialManager(db)
    # a body that tries to retarget another agent must NOT win — path agent_id pins
    asyncio.run(mgr.save_raw("agent_x", {"agent_id": "attacker", "app_id": "cli_new"}))
    assert db.updated["agent_id"] == "agent_x"
    assert db.updated["app_id"] == "cli_new"
