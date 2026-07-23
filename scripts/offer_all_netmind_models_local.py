"""
@file_name: offer_all_netmind_models_local.py
@author:
@date: 2026-07-23
@description: Local dev helper — make EVERY catalogued aggregator model
selectable on a local install, without any source change.

Why this exists
---------------
A local (`bash run.sh` / DMG) install only offers the models the committed
probe ledger marked as PASS for each protocol. That ledger is a release-time
snapshot probed from one network: models unreachable from THAT network are
stamped `fail` and disappear from the dropdown even though they work here. The
cloud re-probes daily and overwrites every row, so cloud shows far more (e.g.
NetMind anthropic 48/51 vs local 23/35). `user_providers.models` accepts an
arbitrary list (the API does not validate it against the ledger), so this tool
just writes the full catalogue straight into the netmind / system_pool rows.

What it does
------------
For each `netmind` / `system_pool` provider row it sets `models` to the row's
CURRENT list first, then appends every catalogued id the row is missing. Keeping
the existing order first is deliberate: `provider_driver.derive.pick_default_model`
uses `models[0]` as the safe default when auto-repairing a broken slot, so we
must not demote a known-good model out of first place.

The catalogue is read from the committed probe ledger (offline, no network). To
pick up models the aggregator added AFTER the shipped snapshot, refresh the
ledger first (Settings → "Update models", or `python -m
xyz_agent_context.agent_framework.model_sync` with a key in the env) and re-run.

Caveats (by design)
-------------------
- Do NOT click "Update models" in the UI afterwards: it re-probes and overwrites
  the row back to pass-only, undoing this. Re-run this tool if that happens.
- Re-onboarding NetMind resets the row to defaults too — re-run afterwards.
- A wrong-protocol pick (an `openai/*` id on the anthropic row, or vice versa)
  errors at call time. That is the cost of full visibility; switch back to a
  matching model if it happens.

Usage
-----
    python scripts/offer_all_netmind_models_local.py --dry-run
    python scripts/offer_all_netmind_models_local.py
    python scripts/offer_all_netmind_models_local.py --user <user_id>
    python scripts/offer_all_netmind_models_local.py --db /path/to/nexus.db
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path

# Aggregator sources whose rows share the NetMind backend catalogue.
_TARGET_SOURCES = ("netmind", "system_pool")

_DEFAULT_DB = Path.home() / ".narranexus" / "nexus.db"


def _catalogue() -> list[str]:
    """Every model id the committed probe ledger lists for NetMind (sorted)."""
    from xyz_agent_context.agent_framework.model_probe_ledger import load_ledger

    ledger = load_ledger()
    models = ledger.get("sources", {}).get("netmind", {}).get("models", {})
    if not models:
        sys.exit(
            "ledger has no netmind models — refresh it first "
            "(Settings → Update models, or run model_sync with a key set)."
        )
    return sorted(models.keys())


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--db", type=Path, default=_DEFAULT_DB, help=f"sqlite path (default {_DEFAULT_DB})")
    p.add_argument("--user", default=None, help="only update this user_id (default: all users)")
    p.add_argument("--dry-run", action="store_true", help="print the plan without writing")
    return p.parse_args()


def main() -> int:
    args = _parse_args()
    if not args.db.is_file():
        sys.exit(f"db not found: {args.db}")

    catalogue = _catalogue()

    where = f"source IN ({','.join('?' * len(_TARGET_SOURCES))})"
    params: list[str] = list(_TARGET_SOURCES)
    if args.user:
        where += " AND user_id = ?"
        params.append(args.user)

    db = sqlite3.connect(str(args.db))
    try:
        rows = db.execute(
            f"SELECT provider_id, user_id, source, protocol, models FROM user_providers WHERE {where}",
            params,
        ).fetchall()
        if not rows:
            print("no matching netmind / system_pool provider rows.")
            return 0

        changed = 0
        for provider_id, user_id, source, protocol, models_json in rows:
            current = json.loads(models_json or "[]")
            merged = current + [m for m in catalogue if m not in current]
            tag = "" if len(merged) != len(current) else "  (already full)"
            print(f"{provider_id}  {source}/{protocol}  user={user_id}:  {len(current)} -> {len(merged)}{tag}")
            if args.dry_run or len(merged) == len(current):
                continue
            db.execute(
                "UPDATE user_providers SET models = ?, updated_at = datetime('now') WHERE provider_id = ?",
                (json.dumps(merged), provider_id),
            )
            changed += 1

        if args.dry_run:
            print(f"\n[dry-run] {len(rows)} row(s) inspected, catalogue size {len(catalogue)}. Nothing written.")
        else:
            db.commit()
            print(f"\nDone. {changed} row(s) updated (catalogue size {len(catalogue)}). "
                  f"Refresh the Settings page to see them.")
    finally:
        db.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
