#!/usr/bin/env python3
"""
Team SOUL.md -> .nxbundle (multi-agent).

Input: a JSON team spec describing the team metadata + each member agent
(with optional per-agent skill list). Output: a single .nxbundle with
multiple agents/ subdirs, manifest.team populated.

Team-spec JSON schema:
{
  "team_name": "Coordinator Trio",
  "team_description": "Project coordination + content + analysis.",
  "team_color": "#3b82f6",
  "team_intro_md": "...",
  "agents": [
    {
      "soul_md": "/abs/path/to/orion/SOUL.md",
      "name": "Orion",
      "role": "Task coordinator and project orchestrator",
      "category": "productivity",
      "source_path": "agents/productivity/orion/SOUL.md",
      "skills": [
        {"src_dir": "/abs/path/to/cost-optimizer", "name": "cost-optimizer"}
      ]
    },
    {...}
  ]
}

Usage:
    python convert_team.py --team-spec team.json --out team.nxbundle
"""

import argparse
import json
from pathlib import Path

from nxbundle_lib import (
    AgentSpec,
    SkillSpec,
    TeamMeta,
    build_agent_files,
    load_soul,
    write_bundle,
)


def build_team_from_spec(spec: dict, out_path: Path) -> dict:
    team_meta = TeamMeta(
        name=spec["team_name"],
        description=spec.get("team_description", ""),
        color=spec.get("team_color", "#3b82f6"),
        intro_md=spec.get("team_intro_md", ""),
    )
    built_agents = []
    for a in spec["agents"]:
        skills = [
            SkillSpec(src_dir=Path(s["src_dir"]), name=s["name"])
            for s in a.get("skills", [])
        ]
        agent_spec = AgentSpec(
            name=a["name"],
            role=a.get("role", ""),
            category=a["category"],
            soul_md_text=load_soul(Path(a["soul_md"])),
            source_path=a["source_path"],
            skills=skills,
            source_repo=a.get("source_repo", "github:mergisi/awesome-openclaw-agents"),
            source_license=a.get("source_license", "MIT"),
        )
        built_agents.append(build_agent_files(agent_spec))
    return write_bundle(out_path=out_path, agents=built_agents, team=team_meta)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--team-spec", required=True, help="Path to team JSON spec")
    ap.add_argument("--out", required=True)
    args = ap.parse_args()

    spec = json.loads(Path(args.team_spec).read_text(encoding="utf-8"))
    result = build_team_from_spec(spec, Path(args.out))
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
