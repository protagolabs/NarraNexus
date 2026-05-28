#!/usr/bin/env python3
"""
CrewAI crew -> NarraNexus .nxbundle (multi-agent).

Reads a crewAIInc/crewAI-examples-style crew directory:
    <crew-dir>/
      README.md
      src/<pkg>/config/
        agents.yaml      # role/goal/backstory per agent
        tasks.yaml       # description/expected_output per task

Emits a NarraNexus multi-agent bundle:
  - One NarraNexus agent per CrewAI agent (composed awareness from role+goal+backstory)
  - Team metadata in manifest.team
  - Team intro_md describes the workflow (all tasks listed, members listed)

Usage:
    python convert_crewai.py \\
        --crew-dir /tmp/crewAI-examples/crews/marketing_strategy \\
        --out bundles/crewai_marketing_strategy.nxbundle

Notes on the rebrand pass:
    nxbundle_lib.rebrand() is OpenClaw->NarraNexus only. CrewAI content
    doesn't mention OpenClaw, so the pass is a no-op here. The composed
    awareness text we build already says "NarraNexus".
"""

import argparse
import json
import re
import sys
from pathlib import Path

from nxbundle_lib import (
    AgentSpec,
    TeamMeta,
    build_agent_files,
    write_bundle,
)


# ---------------------------------------------------------------------------
# Tiny YAML parser — handles the specific CrewAI shape only
# (top-level keys, 2-space-indented `field: >` folded scalars).
# ---------------------------------------------------------------------------

def parse_crewai_yaml(text: str) -> dict:
    """Parse:
        <key>:
          <subkey>: >
            <multi-line text>
            <more text>
    Returns: {key: {subkey: joined_text}}.
    Blank lines inside a folded block become paragraph breaks ('\\n\\n').
    """
    result: dict = {}
    cur_key: str | None = None
    cur_sub: str | None = None
    cur_lines: list[str] = []

    def flush():
        nonlocal cur_sub, cur_lines
        if cur_key and cur_sub:
            # Group consecutive non-empty lines into paragraphs
            paragraphs: list[list[str]] = [[]]
            for ln in cur_lines:
                if ln == "":
                    if paragraphs[-1]:
                        paragraphs.append([])
                else:
                    paragraphs[-1].append(ln)
            joined = "\n\n".join(" ".join(p).strip() for p in paragraphs if p)
            result.setdefault(cur_key, {})[cur_sub] = joined.strip()
        cur_sub = None
        cur_lines = []

    for raw_line in text.splitlines():
        if raw_line.startswith("#"):
            continue
        # Top-level key (no leading space, ends with `:` only)
        m = re.match(r"^([A-Za-z_][\w]*):\s*$", raw_line)
        if m:
            flush()
            cur_key = m.group(1)
            cur_sub = None
            cur_lines = []
            continue
        # 2-space-indented sub-key with folded scalar `field: >`
        m = re.match(r"^  ([A-Za-z_][\w]*):\s*>\s*$", raw_line)
        if m:
            flush()
            cur_sub = m.group(1)
            cur_lines = []
            continue
        # Inline sub-key `field: value`
        m = re.match(r"^  ([A-Za-z_][\w]*):\s+(.+)$", raw_line)
        if m:
            flush()
            cur_sub = m.group(1)
            cur_lines = [m.group(2)]
            flush()
            continue
        # Content line — anything inside a folded block
        if cur_sub is not None:
            stripped = raw_line.strip()
            cur_lines.append(stripped)

    flush()
    return result


# ---------------------------------------------------------------------------
# Crew dir navigation
# ---------------------------------------------------------------------------

def find_config_dir(crew_dir: Path) -> Path:
    src = crew_dir / "src"
    if not src.is_dir():
        raise FileNotFoundError(f"no src/ in {crew_dir}")
    for pkg in src.iterdir():
        if pkg.is_dir() and (pkg / "config").is_dir():
            return pkg / "config"
    raise FileNotFoundError(f"no <pkg>/config/ under {src}")


# ---------------------------------------------------------------------------
# Composition: CrewAI agent -> NarraNexus awareness markdown
# ---------------------------------------------------------------------------

def display_name(agent_id: str) -> str:
    return agent_id.replace("_", " ").title()


def build_awareness(agent_id: str, agent_data: dict, all_tasks: dict, team_members: list[str]) -> str:
    name = display_name(agent_id)
    role = agent_data.get("role", "").strip()
    goal = agent_data.get("goal", "").strip()
    backstory = agent_data.get("backstory", "").strip()

    other_members = [display_name(m) for m in team_members if m != agent_id]
    teammates_line = ", ".join(other_members) if other_members else "(solo)"

    lines = [
        f"# {name}",
        "",
        f"You are the **{role}** on a NarraNexus agent team.",
        "",
        "## Role",
        role or "(not specified)",
        "",
        "## Goal",
        goal or "(not specified)",
        "",
        "## Backstory",
        backstory or "(not specified)",
        "",
        "## Team",
        f"Your teammates: {teammates_line}.",
        "Use @-mention or NarraNexus MessageBus to hand off work between teammates.",
        "",
        "## Team Workflow",
        "The team operates over the following tasks. Your responsibilities may include any of these — coordinate with teammates to decide who does what:",
        "",
    ]
    for tid, tdata in all_tasks.items():
        desc = (tdata.get("description") or "").strip()
        out = (tdata.get("expected_output") or "").strip()
        first_para = desc.split("\n\n")[0]
        short_desc = first_para[:240] + ("..." if len(first_para) > 240 else "")
        lines.append(f"- **{tid}** — {short_desc}")
        if out:
            short_out = out[:160] + ("..." if len(out) > 160 else "")
            lines.append(f"  - *Expected output:* {short_out}")
    lines.append("")
    return "\n".join(lines)


def extract_readme_intro(readme: str) -> str:
    """Pull the Introduction section (## Introduction ... before next ##)."""
    if not readme:
        return ""
    lines = readme.splitlines()
    out: list[str] = []
    in_intro = False
    for ln in lines:
        if re.match(r"^##\s+Introduction\b", ln, re.IGNORECASE):
            in_intro = True
            continue
        if in_intro and ln.startswith("##"):
            break
        if in_intro:
            out.append(ln)
    return "\n".join(out).strip()


def build_team_intro(crew_slug: str, readme: str, tasks: dict, agents: dict) -> str:
    title = display_name(crew_slug) + " Crew"
    intro = extract_readme_intro(readme)

    lines = [f"## {title}", ""]
    if intro:
        lines.append(intro)
        lines.append("")
    lines.append(f"### Members ({len(agents)})")
    for aid, adata in agents.items():
        lines.append(f"- **{display_name(aid)}** — {adata.get('role','').strip()}")
    lines.append("")
    lines.append(f"### Workflow ({len(tasks)} tasks)")
    for tid, tdata in tasks.items():
        first_para = (tdata.get("description") or "").strip().split("\n\n")[0]
        short = first_para[:140] + ("..." if len(first_para) > 140 else "")
        lines.append(f"- `{tid}` — {short}")
    lines.append("")
    lines.append("---")
    lines.append("")
    lines.append("Imported from CrewAI examples (`github:crewAIInc/crewAI-examples`). The original CrewAI framework defines task→agent assignments in `crew.py` (Python). On NarraNexus, agents coordinate dynamically via MessageBus.")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--crew-dir", required=True, help="Path to crewAI-examples/crews/<crew_name>")
    ap.add_argument("--crew-slug", default="", help="Override slug for source attribution")
    ap.add_argument("--source-repo", default="github:crewAIInc/crewAI-examples")
    ap.add_argument("--source-license", default="MIT")
    ap.add_argument("--team-color", default="#10b981")
    ap.add_argument("--out", required=True)
    args = ap.parse_args()

    crew_dir = Path(args.crew_dir).resolve()
    crew_slug = args.crew_slug or crew_dir.name

    config_dir = find_config_dir(crew_dir)
    agents_yaml = (config_dir / "agents.yaml").read_text(encoding="utf-8")
    tasks_yaml = (config_dir / "tasks.yaml").read_text(encoding="utf-8")
    readme = (crew_dir / "README.md").read_text(encoding="utf-8") if (crew_dir / "README.md").is_file() else ""

    agents = parse_crewai_yaml(agents_yaml)
    tasks = parse_crewai_yaml(tasks_yaml)
    if not agents:
        sys.exit("ERROR: failed to parse agents.yaml")

    team = TeamMeta(
        name=display_name(crew_slug) + " Crew",
        description=f"Multi-agent crew imported from CrewAI examples ({crew_slug}). {len(agents)} agents, {len(tasks)} tasks.",
        color=args.team_color,
        intro_md=build_team_intro(crew_slug, readme, tasks, agents),
    )

    team_members = list(agents.keys())
    built_agents = []
    for aid, adata in agents.items():
        awareness_text = build_awareness(aid, adata, tasks, team_members)
        spec = AgentSpec(
            name=display_name(aid),
            role=adata.get("role", "").strip(),
            category=f"crewai-{crew_slug}",
            soul_md_text=awareness_text,  # field name is legacy; this is our composed awareness
            source_path=f"crews/{crew_slug}/src/<pkg>/config/agents.yaml#{aid}",
            skills=[],  # CrewAI tools live in Python crew.py — out of POC scope
            source_repo=args.source_repo,
            source_license=args.source_license,
        )
        built_agents.append(build_agent_files(spec))

    result = write_bundle(
        out_path=Path(args.out),
        agents=built_agents,
        team=team,
        bundle_info_extra=[
            f"Source CrewAI crew: {crew_slug}",
            f"Tasks defined: {len(tasks)}",
            "CrewAI tools (SerperDevTool, ScrapeWebsiteTool, etc.) NOT imported — they live in crew.py",
        ],
    )
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
