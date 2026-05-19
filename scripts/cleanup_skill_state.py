#!/usr/bin/env python3
"""
Clean up all state that a skill benchmark run can produce.

State categories handled:
  L1  filesystem  — skills/<name>/, all files including pip --target / npm libs
  L2  database    — instance_jobs created by the test agent (cancelled, not
                    deleted, to keep audit trail)
  L3  skill_meta  — embedded inside L1 (rm -rf the parent dir)

Intentionally NOT handled (per design discussion):
  L4  global installs (brew install, npm install -g) — agent forgets them
                    once the skill dir is gone; system pollution is accepted.
  L5  external SaaS state — irreversible; skills causing L5 are excluded
                    from the candidate list by policy.

Usage:
    # Clean a single agent's workspace + jobs
    .venv/bin/python scripts/cleanup_skill_state.py --agent-id agent_skill_xxx

    # Clean ALL test agents (pattern: agent_skill_*)
    .venv/bin/python scripts/cleanup_skill_state.py --all-test-agents

    # Dry run (show what would be deleted)
    .venv/bin/python scripts/cleanup_skill_state.py --all-test-agents --dry-run
"""

from __future__ import annotations

import argparse
import shutil
import sqlite3
import sys
from pathlib import Path

DB_PATH = Path.home() / ".narranexus" / "nexus.db"
WORKSPACE_BASE = Path.home() / ".nexusagent" / "workspaces"
TEST_AGENT_PREFIX = "agent_skill_"


def list_test_agent_workspaces() -> list[Path]:
    """Find every workspace dir whose agent_id matches the test prefix."""
    if not WORKSPACE_BASE.exists():
        return []
    matches: list[Path] = []
    for child in WORKSPACE_BASE.iterdir():
        if child.is_dir() and child.name.startswith(TEST_AGENT_PREFIX):
            matches.append(child)
    return sorted(matches)


def cleanup_filesystem(agent_workspace: Path, *, dry_run: bool) -> dict:
    """Wipe the per-agent workspace dir, returning a summary."""
    skills_dir = agent_workspace / "skills"
    summary = {"workspace": str(agent_workspace), "removed_skills": [], "removed_workspace": False}

    if skills_dir.exists():
        for skill_path in skills_dir.iterdir():
            if skill_path.is_dir():
                summary["removed_skills"].append(skill_path.name)
                if not dry_run:
                    shutil.rmtree(skill_path)

    if agent_workspace.exists():
        if not dry_run:
            shutil.rmtree(agent_workspace)
        summary["removed_workspace"] = True
    return summary


def cleanup_jobs(agent_ids: list[str], *, dry_run: bool) -> dict:
    """Cancel jobs owned by the test agents. We cancel rather than delete
    so the audit trail remains queryable."""
    if not agent_ids:
        return {"cancelled": 0, "agents": []}

    conn = sqlite3.connect(DB_PATH)
    try:
        placeholder = ",".join(["?"] * len(agent_ids))
        rows = conn.execute(
            f"SELECT agent_id, COUNT(*) FROM instance_jobs "
            f"WHERE agent_id IN ({placeholder}) "
            f"AND status NOT IN ('cancelled', 'completed', 'failed') "
            f"GROUP BY agent_id",
            agent_ids,
        ).fetchall()
        per_agent = {a: n for a, n in rows}
        total = sum(per_agent.values())

        if total > 0 and not dry_run:
            conn.execute(
                f"UPDATE instance_jobs SET status='cancelled' "
                f"WHERE agent_id IN ({placeholder}) "
                f"AND status NOT IN ('cancelled', 'completed', 'failed')",
                agent_ids,
            )
            conn.commit()
        return {"cancelled": total, "per_agent": per_agent}
    finally:
        conn.close()


def cleanup_module_instances(agent_ids: list[str], *, dry_run: bool) -> dict:
    """Soft-delete module instance records (e.g. SkillModule instance)
    so they don't accumulate. Only the per-test-agent ones."""
    if not agent_ids:
        return {"removed": 0}
    conn = sqlite3.connect(DB_PATH)
    try:
        placeholder = ",".join(["?"] * len(agent_ids))
        row = conn.execute(
            f"SELECT COUNT(*) FROM module_instances "
            f"WHERE agent_id IN ({placeholder})",
            agent_ids,
        ).fetchone()
        count = row[0] if row else 0
        if count > 0 and not dry_run:
            conn.execute(
                f"DELETE FROM module_instances WHERE agent_id IN ({placeholder})",
                agent_ids,
            )
            conn.commit()
        return {"removed": count}
    finally:
        conn.close()


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    g = p.add_mutually_exclusive_group(required=True)
    g.add_argument("--agent-id", help="Specific agent_id to clean")
    g.add_argument("--all-test-agents", action="store_true", help="Clean every agent matching agent_skill_*")
    p.add_argument("--dry-run", action="store_true", help="Report what would be removed, do not delete")
    args = p.parse_args()

    if args.all_test_agents:
        workspaces = list_test_agent_workspaces()
        agent_ids = [w.name for w in workspaces]
    else:
        agent_ids = [args.agent_id]
        workspaces = [WORKSPACE_BASE / args.agent_id]

    if not agent_ids:
        print("No matching agents found.")
        sys.exit(0)

    label = "DRY-RUN" if args.dry_run else "CLEANUP"
    print(f"=== {label}: {len(agent_ids)} agent(s) ===\n")

    for ws in workspaces:
        agent_id = ws.name
        print(f"--- {agent_id} ---")
        fs = cleanup_filesystem(ws, dry_run=args.dry_run)
        print(f"  filesystem: removed_skills={fs['removed_skills']}, workspace_removed={fs['removed_workspace']}")
        print()

    jobs = cleanup_jobs(agent_ids, dry_run=args.dry_run)
    print(f"jobs cancelled: {jobs['cancelled']}")
    if jobs.get("per_agent"):
        for a, n in jobs["per_agent"].items():
            print(f"  {a}: {n}")

    instances = cleanup_module_instances(agent_ids, dry_run=args.dry_run)
    print(f"module_instances removed: {instances['removed']}")

    print(f"\nDone ({label}).")


if __name__ == "__main__":
    main()
