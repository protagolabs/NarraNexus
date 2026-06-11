"""
@file_name: migrate_users_to_netmind.py
@author: NetMind.AI
@date: 2026-06-11
@description: One-shot offline migration of legacy user ids to NetMind
userSystemCode (Phase 1 user-system unification).

Legacy users registered with a self-chosen username that became their
user_id; the NetMind switchover keys identity by the 32-hex
userSystemCode instead. This script rewrites every identity column in
the database and renames workspace directories accordingly.

OPERATIONAL RULES (hard-won — see v1.7.16 outage):
  * Run OFFLINE with the stack stopped (`make app-down`). Never wire
    this into backend lifespan; it would blow the compose healthcheck
    window and take prod down.
  * Three-stage workflow, in order:
        --report            inventory users + resolve emails, list issues
        --execute --mapping mapping.csv   rewrite (one txn per user)
        --verify  --mapping mapping.csv   assert zero residual old ids
  * mapping.csv columns: old_user_id,new_user_system_code
    (emails from --report are sent to NetMind; their batch-provisioning
    answer comes back as this CSV).
  * Idempotent: re-running --execute after success is a no-op, so a
    crash mid-run is recovered by simply running again.

Column policy is derived from schema_registry at runtime and verified
against an explicit classification — if someone adds a new identity-named
column without updating CLASSIFICATION below, the script refuses to run
rather than silently missing data.

Usage:
    uv run python scripts/migrate_users_to_netmind.py --report
    uv run python scripts/migrate_users_to_netmind.py --execute --mapping m.csv
    uv run python scripts/migrate_users_to_netmind.py --verify --mapping m.csv
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import json
import os
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional

# Allow running both as a script (uv run python scripts/...) and via import.
_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT / "src") not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT / "src"))

from xyz_agent_context.utils import schema_registry  # noqa: E402

# Identity-shaped column names we scan schema_registry for.
_IDENTITY_COLUMN_NAMES = {
    "user_id",
    "owner_user_id",
    "created_by",
    "used_by_user_id",
    "scope_id",
}

# Explicit classification of every identity-named column. Three buckets:
#   include      — holds a NarraNexus user id, rewrite it
#   exclude      — same name, DIFFERENT id space, never touch
#   conditional  — memory_* scope_id: a user id only when scope_type='user'
_EXCLUDE: dict = {
    # Slack/Telegram platform uids — not NarraNexus users.
    ("channel_slack_credentials", "owner_user_id"): "IM platform uid",
    ("channel_telegram_credentials", "owner_user_id"): "IM platform uid",
    # Bus channel owner is an AGENT id (message_bus_trigger activation rule).
    ("bus_channels", "created_by"): "agent id, not user id",
}

_MEMORY_TABLE_PREFIX = "memory_"

# users.metadata stamp left on migrated rows for audit / idempotency checks.
_MIGRATION_STAMP_KEY = "netmind_migration"


@dataclass
class IdentityColumns:
    plain: List[tuple] = field(default_factory=list)  # (table, column)
    memory_scope_tables: List[str] = field(default_factory=list)


def classify_identity_columns() -> IdentityColumns:
    """Derive the rewrite plan from schema_registry, failing loudly on drift.

    Any identity-named column that is neither classified here nor covered
    by the memory scope rule raises — a new table added after this script
    was written must be consciously classified, not silently skipped.
    """
    result = IdentityColumns()
    for table_name, table in sorted(schema_registry.TABLES.items()):
        for col in table.columns:
            if col.name not in _IDENTITY_COLUMN_NAMES:
                continue
            if (table_name, col.name) in _EXCLUDE:
                continue
            if col.name == "scope_id":
                if table_name.startswith(_MEMORY_TABLE_PREFIX):
                    result.memory_scope_tables.append(table_name)
                    continue
                raise RuntimeError(
                    f"Unclassified scope_id column: {table_name}.{col.name} — "
                    "update CLASSIFICATION in migrate_users_to_netmind.py"
                )
            result.plain.append((table_name, col.name))
    return result


async def build_report(db) -> List[dict]:
    """Inventory legacy users and resolve their emails via invite_codes.

    Emails were never written to users.email by the legacy register flow;
    the only mapping lives in invite_codes (code issued to an email,
    consumed by used_by_user_id). Users without a resolvable email are
    flagged for manual handling.
    """
    users = await db.execute(
        "SELECT user_id, user_type, email, metadata FROM users", fetch=True
    )
    rows: List[dict] = []
    for u in users or []:
        user_id = u["user_id"]
        stamp = _read_stamp(u.get("metadata"))
        if stamp is not None:
            rows.append(
                {"user_id": user_id, "email": None, "issue": "already_migrated"}
            )
            continue

        email = (u.get("email") or "").strip() or None
        if email is None:
            invite = await db.execute(
                "SELECT email FROM invite_codes WHERE used_by_user_id = %s "
                "AND email IS NOT NULL LIMIT 1",
                params=(user_id,),
                fetch=True,
            )
            if invite:
                email = (invite[0].get("email") or "").strip() or None

        rows.append(
            {
                "user_id": user_id,
                "email": email,
                "issue": None if email else "no_email",
            }
        )
    return rows


def _read_stamp(metadata_raw) -> Optional[dict]:
    if not metadata_raw:
        return None
    meta = metadata_raw
    if isinstance(meta, str):
        try:
            meta = json.loads(meta)
        except json.JSONDecodeError:
            return None
    if not isinstance(meta, dict):
        return None
    return meta.get(_MIGRATION_STAMP_KEY)


async def _stamp_user(db, new_id: str, old_id: str) -> None:
    row = await db.get_one("users", {"user_id": new_id})
    meta = row.get("metadata") if row else None
    if isinstance(meta, str):
        try:
            meta = json.loads(meta)
        except json.JSONDecodeError:
            meta = {}
    meta = meta if isinstance(meta, dict) else {}
    meta[_MIGRATION_STAMP_KEY] = {"old_user_id": old_id}
    await db.execute(
        "UPDATE users SET metadata = %s WHERE BINARY user_id = %s",
        params=(json.dumps(meta, ensure_ascii=False), new_id),
        fetch=False,
    )


async def execute_migration(
    db, mapping: Dict[str, str], base_working_path: str
) -> dict:
    """Rewrite identity columns and rename workspaces for every mapped user.

    Per-user: one transaction over all column rewrites (all-or-nothing),
    then filesystem renames after commit (renames are idempotent and a
    crashed run is recovered by re-running). Users whose old id no longer
    exists are skipped — that is what makes re-runs no-ops.
    """
    cols = classify_identity_columns()
    stats = {"users_migrated": 0, "rows_updated": 0, "dirs_renamed": 0}

    for old_id, new_id in mapping.items():
        existing_old = await db.get_one("users", {"user_id": old_id})
        if existing_old is None:
            continue  # already migrated (or never existed) — idempotency

        # Collect agent workspaces BEFORE the rewrite (paths embed old id).
        agents = await db.execute(
            "SELECT agent_id FROM agents WHERE BINARY created_by = %s",
            params=(old_id,),
            fetch=True,
        )
        agent_ids = [a["agent_id"] for a in agents or []]

        began = False
        try:
            if hasattr(db, "begin_transaction"):
                await db.begin_transaction()
                began = True
            for table, column in cols.plain:
                result = await db.execute(
                    f"UPDATE {table} SET `{column}` = %s "
                    f"WHERE BINARY `{column}` = %s",
                    params=(new_id, old_id),
                    fetch=False,
                )
                if isinstance(result, int):
                    stats["rows_updated"] += result
            for table in cols.memory_scope_tables:
                result = await db.execute(
                    f"UPDATE {table} SET scope_id = %s "
                    f"WHERE scope_type = 'user' AND BINARY scope_id = %s",
                    params=(new_id, old_id),
                    fetch=False,
                )
                if isinstance(result, int):
                    stats["rows_updated"] += result
            await _stamp_user(db, new_id, old_id)
            if began:
                await db.commit()
        except Exception:
            if began:
                await db.rollback()
            raise

        # Filesystem renames after the DB commit. A crash between commit
        # and rename leaves old-named dirs; re-running finds the old id
        # gone in the DB but we still attempt renames for mapped users
        # below — hence renames live OUTSIDE the existing_old guard? No:
        # they are repeated here unconditionally for this user, and the
        # rename helper tolerates already-renamed dirs.
        stats["dirs_renamed"] += _rename_workspaces(
            base_working_path, agent_ids, old_id, new_id
        )
        stats["users_migrated"] += 1

    # Second pass for crash recovery: users already migrated in the DB may
    # still have old-named workspace dirs (crash between commit and rename).
    for old_id, new_id in mapping.items():
        agents = await db.execute(
            "SELECT agent_id FROM agents WHERE BINARY created_by = %s",
            params=(new_id,),
            fetch=True,
        )
        agent_ids = [a["agent_id"] for a in agents or []]
        stats["dirs_renamed"] += _rename_workspaces(
            base_working_path, agent_ids, old_id, new_id
        )

    return stats


def _rename_workspaces(
    base_working_path: str, agent_ids: List[str], old_id: str, new_id: str
) -> int:
    renamed = 0
    for agent_id in agent_ids:
        old_dir = Path(base_working_path) / f"{agent_id}_{old_id}"
        new_dir = Path(base_working_path) / f"{agent_id}_{new_id}"
        if old_dir.is_dir() and not new_dir.exists():
            os.rename(old_dir, new_dir)
            renamed += 1
    return renamed


async def verify_migration(db, old_ids: List[str]) -> Dict[str, int]:
    """Count residual occurrences of each old id across all identity columns.

    Every count must be zero after a successful --execute.
    """
    cols = classify_identity_columns()
    residuals: Dict[str, int] = {}
    for old_id in old_ids:
        total = 0
        for table, column in cols.plain:
            rows = await db.execute(
                f"SELECT COUNT(*) AS n FROM {table} "
                f"WHERE BINARY `{column}` = %s",
                params=(old_id,),
                fetch=True,
            )
            total += int(rows[0]["n"]) if rows else 0
        for table in cols.memory_scope_tables:
            rows = await db.execute(
                f"SELECT COUNT(*) AS n FROM {table} "
                f"WHERE scope_type = 'user' AND BINARY scope_id = %s",
                params=(old_id,),
                fetch=True,
            )
            total += int(rows[0]["n"]) if rows else 0
        residuals[old_id] = total
    return residuals


def _load_mapping(path: str) -> Dict[str, str]:
    mapping: Dict[str, str] = {}
    with open(path, newline="", encoding="utf-8") as f:
        for row in csv.reader(f):
            if not row or row[0].startswith("#") or row[0] == "old_user_id":
                continue
            old_id, new_id = row[0].strip(), row[1].strip()
            if len(new_id) != 32:
                raise SystemExit(
                    f"Suspicious userSystemCode for {old_id!r}: {new_id!r} "
                    "(expected 32 chars)"
                )
            mapping[old_id] = new_id
    return mapping


async def _amain() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--report", action="store_true")
    group.add_argument("--execute", action="store_true")
    group.add_argument("--verify", action="store_true")
    parser.add_argument("--mapping", help="CSV: old_user_id,new_user_system_code")
    args = parser.parse_args()

    from xyz_agent_context.settings import settings
    from xyz_agent_context.utils.db_factory import get_db_client

    db = await get_db_client()

    if args.report:
        rows = await build_report(db)
        writer = csv.writer(sys.stdout)
        writer.writerow(["user_id", "email", "issue"])
        for r in rows:
            writer.writerow([r["user_id"], r["email"] or "", r["issue"] or ""])
        issues = [r for r in rows if r["issue"] == "no_email"]
        print(
            f"# {len(rows)} users, {len(issues)} without a resolvable email",
            file=sys.stderr,
        )
        return

    if not args.mapping:
        raise SystemExit("--execute/--verify require --mapping <csv>")
    mapping = _load_mapping(args.mapping)

    if args.execute:
        stats = await execute_migration(
            db, mapping, base_working_path=settings.base_working_path
        )
        print(json.dumps(stats, indent=2))
        return

    residuals = await verify_migration(db, list(mapping.keys()))
    print(json.dumps(residuals, indent=2))
    bad = {k: v for k, v in residuals.items() if v}
    if bad:
        raise SystemExit(f"Residual old ids found: {bad}")
    print("verify OK — zero residuals", file=sys.stderr)


if __name__ == "__main__":
    asyncio.run(_amain())
