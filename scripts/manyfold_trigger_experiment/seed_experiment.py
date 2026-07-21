#!/usr/bin/env python3
"""
@file_name: seed_experiment.py
@author: rujing.yan
@date: 2026-07-20
@description: Seed minimal data for the "trigger 走 Manyfold" experiment.

Reuses an EXISTING local agent (which already has a working provider slot, so
it can actually run an LLM) and adds the two things the experiment needs:

  1. An operational-looking Lark credential row for the agent, so LarkModule
     renders "Mode: LARK CHANNEL — reply via lark_cli" (the app_secret is fake;
     the abstract-logic run only asserts the agent EMITS the lark_cli call to
     the right room — a real send needs a real bot, the optional phase-2).
  2. A couple of non-terminal SCHEDULED jobs due now, so the platform "clock"
     has something to mirror and fire.

Run against the same DATABASE_URL the app uses (default sqlite
~/.narranexus/nexus.db). Idempotent: re-running replaces the seeded rows.

Usage:
  python -m scripts.manyfold_trigger_experiment.seed_experiment --list-agents
  python -m scripts.manyfold_trigger_experiment.seed_experiment --agent agent_692500eadd68
  python -m scripts.manyfold_trigger_experiment.seed_experiment --agent <id> --clean
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import json
import uuid

from xyz_agent_context.repository.job_repository import JobRepository
from xyz_agent_context.schema.job_schema import JobType, TriggerConfig
from xyz_agent_context.utils import utc_now
from xyz_agent_context.utils.db_factory import get_db_client

# Marker so seeded rows are recognizable / removable.
_SEED_APP_ID = "cli_experiment_fake"
_OWNER_OPEN_ID = "ou_alice"  # send-im --sender ou_alice → treated as the owner


async def _resolve_agent(db, agent_id: str | None) -> tuple[str, str]:
    """Return (agent_id, user_id). Picks the first agent with a bound slot if
    none given."""
    if agent_id:
        row = await db.get_one("agents", {"agent_id": agent_id})
        if not row:
            raise SystemExit(f"agent {agent_id!r} not found")
        return agent_id, row["created_by"]
    # Prefer an agent that has a provider slot (can run an LLM).
    slots = await db.get("agent_slots", {}) or []
    for slot in slots:
        row = await db.get_one("agents", {"agent_id": slot["agent_id"]})
        if row:
            return row["agent_id"], row["created_by"]
    raise SystemExit("no agent with a provider slot found — create one in the UI first")


async def list_agents() -> None:
    db = await get_db_client()
    agents = await db.get("agents", {}) or []
    slots = {s["agent_id"] for s in (await db.get("agent_slots", {}) or [])}
    print(f"{len(agents)} agent(s):")
    for a in agents:
        has = "provider✓" if a["agent_id"] in slots else "no-provider"
        print(f"  · {a['agent_id']}  owner={a['created_by']}  {has}")


async def seed_lark_credential(db, agent_id: str) -> None:
    await db.delete("lark_credentials", {"agent_id": agent_id})
    now = utc_now().isoformat()
    await db.insert(
        "lark_credentials",
        {
            "agent_id": agent_id,
            "app_id": _SEED_APP_ID,
            "app_secret_ref": "appsecret:experiment",
            # base64 non-empty → receive_enabled()=True (fake secret).
            "app_secret_encrypted": base64.b64encode(b"fake_secret").decode(),
            "brand": "feishu",
            "profile_name": f"agent_{agent_id}",
            "workspace_path": "",
            "bot_name": "Experiment Bot",
            "owner_open_id": _OWNER_OPEN_ID,
            "owner_name": "Alice",
            # bot-active status (not 'expired'); passes get_active_credentials.
            "auth_status": "user_logged_in",
            "is_active": 1,
            # user_oauth_completed_at → current_click_stage()=='completed', so
            # the coach says "reply", not "configure".
            "permission_state": json.dumps(
                {
                    "user_oauth_completed_at": now,
                    "availability_confirmed": True,
                    "bot_scopes_confirmed": True,
                }
            ),
            "created_at": now,
            "updated_at": now,
        },
    )
    print(f"  seeded lark_credentials for {agent_id} (owner_open_id={_OWNER_OPEN_ID})")


async def seed_jobs(db, agent_id: str, user_id: str, count: int) -> list[str]:
    repo = JobRepository(db)
    job_ids: list[str] = []
    for i in range(count):
        job_id = f"job_mfexp_{uuid.uuid4().hex[:8]}"
        await repo.create_job(
            agent_id=agent_id,
            user_id=user_id,
            job_id=job_id,
            title=f"[mf-exp] heartbeat {i + 1}",
            description="Experiment job for trigger externalization validation.",
            job_type=JobType.SCHEDULED,
            trigger_config=TriggerConfig(interval_seconds=3600, timezone="Asia/Shanghai"),
            payload="Reply with a one-line status: experiment job ran.",
            instance_id=f"job_{job_id}",
            next_run_time=utc_now(),  # due now
        )
        job_ids.append(job_id)
    print(f"  seeded {count} scheduled job(s): {', '.join(job_ids)}")
    return job_ids


async def clean(db, agent_id: str) -> None:
    await db.delete("lark_credentials", {"agent_id": agent_id})
    jobs = await db.get("instance_jobs", {"agent_id": agent_id}) or []
    removed = 0
    for j in jobs:
        if str(j.get("title", "")).startswith("[mf-exp]"):
            await db.delete("instance_jobs", {"job_id": j["job_id"]})
            removed += 1
    print(f"  removed lark cred + {removed} seeded job(s) for {agent_id}")


async def _main(args) -> None:
    if args.list_agents:
        await list_agents()
        return
    db = await get_db_client()
    agent_id, user_id = await _resolve_agent(db, args.agent)
    if args.clean:
        await clean(db, agent_id)
        return
    print(f"seeding experiment data for agent={agent_id} owner={user_id}")
    await seed_lark_credential(db, agent_id)
    job_ids = await seed_jobs(db, agent_id, user_id, args.jobs)
    print("\nready. Use with fake_manyfold.py:")
    print(f"  export EXP_AGENT={agent_id}")
    print(f"  python fake_manyfold.py send-im --agent {agent_id} --provider lark "
          f"--room oc_test --sender {_OWNER_OPEN_ID} --sender-name Alice "
          f"--text 'what is the weather tomorrow?'")
    if job_ids:
        print(f"  python fake_manyfold.py fire-job --agent {agent_id} --job {job_ids[0]}")


def main() -> None:
    p = argparse.ArgumentParser(description="Seed data for the Manyfold trigger experiment")
    p.add_argument("--agent", default=None, help="existing agent_id (default: first with a slot)")
    p.add_argument("--jobs", type=int, default=2, help="how many scheduled jobs to seed")
    p.add_argument("--list-agents", action="store_true", help="list agents and exit")
    p.add_argument("--clean", action="store_true", help="remove seeded rows and exit")
    asyncio.run(_main(p.parse_args()))


if __name__ == "__main__":
    main()
