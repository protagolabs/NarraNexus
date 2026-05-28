#!/usr/bin/env python3
"""
Single-agent SOUL.md -> .nxbundle, with optional bundled skills.

Usage:
    python convert_single.py \\
        --soul /path/to/SOUL.md \\
        --name "Lens" \\
        --role "Code reviewer and quality gatekeeper" \\
        --category development \\
        --source-path agents/development/code-reviewer/SOUL.md \\
        --skill-dir /path/to/git-commit-writer:git-commit-writer \\
        --skill-dir /path/to/excalidraw-architecture:excalidraw-architecture \\
        --out lens.nxbundle

--skill-dir is repeatable; format is "PATH:NAME". NAME is the folder name
that appears under skills/ inside workspace.tar.gz.
"""

import argparse
import json
from pathlib import Path

from nxbundle_lib import (
    AgentSpec,
    SkillSpec,
    build_agent_files,
    load_soul,
    write_bundle,
)


def parse_skill_arg(s: str) -> SkillSpec:
    if ":" in s:
        path_part, name_part = s.split(":", 1)
    else:
        path_part = s
        name_part = Path(path_part).name
    return SkillSpec(src_dir=Path(path_part), name=name_part)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--soul", required=True)
    ap.add_argument("--name", required=True)
    ap.add_argument("--role", default="")
    ap.add_argument("--category", required=True)
    ap.add_argument("--source-repo", default="github:mergisi/awesome-openclaw-agents")
    ap.add_argument("--source-path", required=True)
    ap.add_argument("--source-license", default="MIT")
    ap.add_argument("--skill-dir", action="append", default=[],
                    help='Repeatable. Format "PATH:NAME" or just PATH '
                         '(NAME defaults to basename).')
    ap.add_argument("--out", required=True)
    args = ap.parse_args()

    skills = [parse_skill_arg(s) for s in args.skill_dir]
    spec = AgentSpec(
        name=args.name,
        role=args.role,
        category=args.category,
        soul_md_text=load_soul(Path(args.soul)),
        source_path=args.source_path,
        skills=skills,
        source_repo=args.source_repo,
        source_license=args.source_license,
    )
    built = build_agent_files(spec)
    result = write_bundle(out_path=Path(args.out), agents=[built])
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
