"""
@file_name: seed_netmind_keys.py
@author: NetMind.AI
@date: 2026-06-03
@description: Post-import seed for v4 bundle's NetMind skills.

Run ONCE after importing `web_development_v4.nxbundle`.

Reads `NETMIND_API_KEY` (from env var or `.env`) and writes it into the
freshly-imported Web Developer agent's `netmind-image-gen` +
`netmind-video-gen` skill env_configs via `SkillModule.set_skill_env_config`
(which uses a merge-style write — safe).

Why this script is needed:
    `SkillModule.install_skill()` overwrites the skill's `.skill_meta.json`
    during install (see `_save_skill_meta` in `skill_module.py`). That means
    any `env_config` shipped INSIDE the bundle's skill zip is destroyed.
    Until the platform either: (a) makes `_save_skill_meta` non-destructive,
    or (b) ships a proper "platform credential injection" path (Option A),
    this script is the temporary bridge.

Usage:
    cd /path/to/NarraNexus
    uv run python scripts/external_agent_import/seed_netmind_keys.py
    # or
    NETMIND_API_KEY=xxx uv run python scripts/external_agent_import/seed_netmind_keys.py

Idempotent — safe to re-run. Targets every agent named "Web Developer"
in the database (so if multiple users imported, each gets seeded).
"""
from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

# Make project src/ importable when run as a script
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from xyz_agent_context.utils.db_factory import get_db_client
from xyz_agent_context.module.skill_module.skill_module import SkillModule


SKILLS_TO_SEED = ("netmind-image-gen", "netmind-video-gen")
TARGET_AGENT_NAME = "Web Developer"


def _load_key() -> str:
    key = os.environ.get("NETMIND_API_KEY", "").strip()
    if key:
        return key
    env_file = ROOT / ".env"
    if env_file.exists():
        for line in env_file.read_text().splitlines():
            if line.startswith("NETMIND_API_KEY="):
                return line.split("=", 1)[1].strip().strip('"').strip("'")
    return ""


async def main() -> int:
    key = _load_key()
    if not key:
        print("ERROR: NETMIND_API_KEY not found in environment or .env")
        return 1
    print(f"Loaded NETMIND_API_KEY (len={len(key)})")

    db = await get_db_client()
    rows = await db.get("agents", filters={"agent_name": TARGET_AGENT_NAME})
    if not rows:
        print(f"No agent named {TARGET_AGENT_NAME!r} found in DB. Import the v4 bundle first.")
        return 1
    print(f"Found {len(rows)} {TARGET_AGENT_NAME!r} agent row(s).")

    failures = 0
    for row in rows:
        agent_id = row["agent_id"]
        user_id = row["created_by"]
        print(f"\n[{agent_id}] (owner={user_id})")
        sm = SkillModule(agent_id=agent_id, user_id=user_id)

        installed = {s.name for s in sm._scan_skills()}
        for skill_name in SKILLS_TO_SEED:
            if skill_name not in installed:
                print(f"  SKIP {skill_name} — not installed for this agent")
                continue
            try:
                sm.set_skill_env_config(skill_name, {"NETMIND_API_KEY": key})
                # Verify the value made it in
                cfg = sm.get_skill_env_config(skill_name)
                if cfg.get("NETMIND_API_KEY"):
                    print(f"  OK   {skill_name} — NETMIND_API_KEY seeded")
                else:
                    print(f"  WARN {skill_name} — set_skill_env_config returned without error but value is missing")
                    failures += 1
            except Exception as e:
                print(f"  FAIL {skill_name} — {e}")
                failures += 1

    print(f"\nDone. {failures} failure(s).")
    return 0 if failures == 0 else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
