"""
@file_name: post_import_setup.py
@author: NetMind.AI
@date: 2026-06-03
@description: Post-import setup for the Web Development v4 bundle.

Run ONCE after importing `web_development_v4.nxbundle`. Does two things:

1. **Seed NETMIND_API_KEY** into Web Developer's netmind-image-gen +
   netmind-video-gen skill env_configs (via `set_skill_env_config` — the
   merge-style writer, so it survives).

2. **Mark all bundle-installed skills as studied** across PM / Web Developer /
   Vercel Deployment Agent / Design Reviewer. The UI shows "Not studied" for
   freshly-imported skills because `_save_skill_meta` rewrites the file with
   only `{source_url, source_type, installed_at}` — no `study_status` field.

   Runtime does NOT gate skill usage on study_status (verified: the
   skills_table the agent sees comes from `requires_env` + `env_configured`
   only, not from study_status). So marking studied is purely cosmetic —
   makes the Skills panel look correct without forcing a real study pass.

Usage:
    cd /path/to/NarraNexus
    uv run python scripts/external_agent_import/post_import_setup.py
    # or supply key via env var:
    NETMIND_API_KEY=xxx uv run python scripts/external_agent_import/post_import_setup.py

Idempotent — safe to re-run. Targets every (agent, skill) pair that should
get marked. Skills not installed for a given agent are silently skipped.
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


# Skills that need NETMIND_API_KEY seeded (on Web Developer)
NETMIND_SKILLS = ("netmind-image-gen", "netmind-video-gen")

# Per-skill canned study summaries. Marked-studied is cosmetic (UI-only) but a
# short useful summary beats an empty one. Anything not in this dict gets a
# generic fallback summary.
STUDY_SUMMARIES = {
    "netmind-image-gen": (
        "# netmind-image-gen\n\n"
        "Generates AI images via NetMind's async `/v1/generation` endpoint "
        "(model: `Qwen/Qwen-Image`).\n\n"
        "**Auth**: `NETMIND_API_KEY` is auto-seeded by the bundle's post-import "
        "setup; no manual config needed.\n\n"
        "**Flow** (see SKILL.md for full curl):\n"
        "1. POST `/v1/generation` with `{model, config:{prompt, image_size}}` -> job_id\n"
        "2. GET `/v1/generation/{id}` repeatedly (~10-15s) until `status=completed`\n"
        "3. Download `result.data[0].url` -> PNG\n\n"
        "**When to invoke**: hero images, section illustrations, OG share images. "
        "The Web Developer agent triggers this proactively when the PRD calls for visuals.\n"
    ),
    "netmind-video-gen": (
        "# netmind-video-gen\n\n"
        "Generates AI videos via NetMind's async `/v1/generation` endpoint "
        "(model: `google/veo3.1-fast`).\n\n"
        "**Auth**: `NETMIND_API_KEY` is auto-seeded by the bundle's post-import "
        "setup; no manual config needed.\n\n"
        "**Flow**: same async pattern as image-gen but slower (~30-90s). "
        "Returns `.mp4` via the same `result.data[0].url` pointer.\n\n"
        "**When to invoke**: only when motion meaningfully lifts the page "
        "(event launch, product demo, atmospheric hero loop). For most pages, "
        "a strong static image + CSS motion beats a generated video.\n"
    ),
    "impeccable": (
        "# impeccable\n\n"
        "Opinionated design system + live design iteration tooling. Used by the "
        "Design Reviewer agent for structural design polish — typography hierarchy, "
        "spacing rhythm, color systems, layout balance.\n\n"
        "Imported via bundle; full study can be re-triggered via the Skills panel "
        "if you want a deeper auto-generated summary.\n"
    ),
    "frontend-design": (
        "# frontend-design\n\n"
        "Modern frontend design patterns + component-level polish (micro-interactions, "
        "motion, refined component anatomy). Used by the Design Reviewer for detail "
        "polish — button states, hover affordances, transition timing, focus rings.\n\n"
        "Imported via bundle; full study can be re-triggered via the Skills panel.\n"
    ),
    "agency-frontend-developer": (
        "# agency-frontend-developer\n\n"
        "Core build skill for the Web Developer agent — HTML/CSS/JS patterns, "
        "accessibility, modern UI conventions.\n\n"
        "Imported via bundle.\n"
    ),
    "vercel-deployments": (
        "# vercel-deployments\n\n"
        "Deploy completed frontend projects to Vercel. Inspect project, identify "
        "framework + package manager, run local build verify, configure project, "
        "deploy, return live URL.\n\n"
        "Imported via bundle.\n"
    ),
    "supabase": (
        "# supabase\n\n"
        "Supabase integration helpers. Used only when the PRD calls for a backend "
        "(auth, persistence). Default is no-backend static sites.\n\n"
        "Imported via bundle.\n"
    ),
    "supabase-postgres-best-practices": (
        "# supabase-postgres-best-practices\n\n"
        "Best-practice patterns for Supabase + Postgres schema design and queries.\n\n"
        "Imported via bundle.\n"
    ),
}

GENERIC_SUMMARY = (
    "# {skill_name}\n\n"
    "Imported via Web Development v4 bundle. Full auto-generated study summary "
    "can be triggered later via the Skills panel.\n"
)


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


# Bundle's expected agent_name -> known skill-name list. Used for warning when
# a skill we expected is missing. Skills not in this list still get processed
# (we walk whatever is actually installed).
EXPECTED = {
    "Project Manager": [],
    "Web Developer": ["agency-frontend-developer", "supabase-postgres-best-practices",
                       "supabase", "netmind-image-gen", "netmind-video-gen"],
    "Vercel Deployment Agent": ["vercel-deployments"],
    "Design Reviewer": ["impeccable", "frontend-design"],
}


async def main() -> int:
    key = _load_key()
    if not key:
        print("WARNING: NETMIND_API_KEY not found in environment or .env — "
              "NetMind skills will be marked-studied but missing the API key.")
        print()
    else:
        print(f"Loaded NETMIND_API_KEY (len={len(key)})")
        print()

    db = await get_db_client()

    total_keyed = 0
    total_studied = 0
    failures = 0

    for agent_name in EXPECTED.keys():
        rows = await db.get("agents", filters={"agent_name": agent_name})
        if not rows:
            print(f"[{agent_name}] not found in DB — skipping")
            continue
        for row in rows:
            agent_id = row["agent_id"]
            user_id = row["created_by"]
            sm = SkillModule(agent_id=agent_id, user_id=user_id)
            installed = sorted({s.name for s in sm._scan_skills()})
            print(f"[{agent_name}] {agent_id} (owner={user_id})")
            print(f"  installed skills: {installed if installed else '(none)'}")

            for skill_name in installed:
                # Seed NETMIND_API_KEY for the two NetMind skills
                if skill_name in NETMIND_SKILLS and key:
                    try:
                        sm.set_skill_env_config(skill_name, {"NETMIND_API_KEY": key})
                        cfg = sm.get_skill_env_config(skill_name)
                        if cfg.get("NETMIND_API_KEY"):
                            print(f"  KEY  {skill_name:35s} NETMIND_API_KEY seeded")
                            total_keyed += 1
                        else:
                            print(f"  WARN {skill_name:35s} set_skill_env_config returned but value missing")
                            failures += 1
                    except Exception as e:
                        print(f"  FAIL {skill_name:35s} env_config: {e}")
                        failures += 1

                # Mark skill as studied (cosmetic — UI signal only)
                summary = STUDY_SUMMARIES.get(skill_name) or GENERIC_SUMMARY.format(skill_name=skill_name)
                try:
                    sm.set_study_status(skill_name, "completed", result=summary)
                    status = sm.get_study_status(skill_name)
                    if status.get("study_status") == "completed":
                        print(f"  STUDY{skill_name:35s} marked as studied")
                        total_studied += 1
                    else:
                        print(f"  WARN {skill_name:35s} set_study_status returned but state=idle")
                        failures += 1
                except Exception as e:
                    print(f"  FAIL {skill_name:35s} study: {e}")
                    failures += 1
            print()

    print("=" * 70)
    print(f"Summary: keys seeded={total_keyed}  studied={total_studied}  failures={failures}")
    return 0 if failures == 0 else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
