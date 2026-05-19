#!/usr/bin/env python3
"""
Skill benchmark — three-phase pipeline.

Phase 1 — Replay (install + study):
    A short scripted dialogue drives the agent to install one real
    open-source skill from a URL, register/configure as required, and
    save a study summary. Uses agent-loop replay so real MCP tool calls
    happen.

Phase 2 — QA (actual skill usage, read-only-ish):
    A small list of "what would a user actually ask this skill?" questions
    is sent through AgentRuntime.run(read_only=True). We log the agent's
    answer + which tools it called.

Phase 3 — Cleanup:
    Always runs (unless --no-cleanup). Wipes the per-agent workspace
    dir and cancels jobs created during the run.

Catalog format
--------------
Each entry in --catalog (YAML) describes one skill to test:

    - id: clawhub-twitter-poster
      name: twitter-poster
      install_url: https://clawhub.ai/openclaw/twitter-poster
      install_kind: clawhub        # clawhub | github | other
      env_to_inject: {}            # optional; given to agent in install dialogue
      qa_questions:
        - What does this skill do?
        - Post a tweet saying "hello from benchmark"
      install_turns:
        - "Please install the twitter-poster skill from https://clawhub.ai/..."
        - "Save the study summary when done."

Usage:
    .venv/bin/python scripts/run_skill_install_and_qa.py \
        --catalog benchmark/skill_examples/skills.yaml \
        --skill-id clawhub-twitter-poster \
        --results-dir data/skill_eval_results/

    # Run all entries in the catalog
    .venv/bin/python scripts/run_skill_install_and_qa.py \
        --catalog benchmark/skill_examples/skills.yaml \
        --results-dir data/skill_eval_results/
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import sys
import time
from pathlib import Path
from typing import Any, Optional

import yaml

os.environ.setdefault("CONVERSATION_DUMP_ENABLED", "1")
_db_dir = Path.home() / ".narranexus"
_db_dir.mkdir(parents=True, exist_ok=True)
os.environ.setdefault("DATABASE_URL", f"sqlite:///{_db_dir / 'nexus.db'}")
os.environ.setdefault("SQLITE_PROXY_URL", "http://localhost:8100")

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT / "src"))
sys.path.insert(0, str(_ROOT))

USER_ID_PREFIX = "user_skill_eval"
AGENT_ID_PREFIX = "agent_skill_"


def _agent_id_from_skill(skill_id: str) -> str:
    safe = re.sub(r"[^a-zA-Z0-9_]", "_", skill_id)[:60]
    return f"{AGENT_ID_PREFIX}{safe}"


# -----------------------------------------------------------------------------
# Phase 1 — Replay (install + study)
# -----------------------------------------------------------------------------

async def run_install_replay(skill_entry: dict, *, dump_dir: Optional[str]) -> dict:
    """Drive the agent to install + study one skill by replaying short dialogue."""
    from xyz_agent_context.agent_runtime import AgentRuntime
    from xyz_agent_context.schema import WorkingSource, ProgressMessage
    from xyz_agent_context.agent_runtime.conversation_dump_service import ConversationDumpService

    skill_id = skill_entry["id"]
    agent_id = _agent_id_from_skill(skill_id)
    user_id = USER_ID_PREFIX
    install_turns: list[str] = skill_entry["install_turns"]

    print(f"\n----- Phase 1 Replay: {skill_id} -----")
    print(f"  agent_id={agent_id} user_id={user_id} turns={len(install_turns)}")

    if dump_dir:
        os.environ["CONVERSATION_DUMP_DIR"] = dump_dir

    turn_records: list[dict] = []
    t0 = time.monotonic()
    for i, user_msg in enumerate(install_turns, start=1):
        print(f"  [turn {i}/{len(install_turns)}] {user_msg[:80]}")
        agent_answer = ""
        try:
            async with AgentRuntime() as runtime:
                async for msg in runtime.run(
                    agent_id=agent_id,
                    user_id=user_id,
                    input_content=user_msg,
                    working_source=WorkingSource.CHAT,
                ):
                    if isinstance(msg, ProgressMessage):
                        tn = (getattr(msg, "details", None) or {}).get("tool_name", "")
                        if tn.endswith("send_message_to_user_directly"):
                            c = msg.details.get("arguments", {}).get("content", "")
                            if c:
                                agent_answer += c
        except Exception as exc:
            print(f"    ! error: {exc}")
            agent_answer = f"[ERROR] {exc}"
        turn_records.append({"turn": i, "user_input": user_msg, "agent_answer_preview": agent_answer[:300]})

    elapsed = time.monotonic() - t0
    return {
        "skill_id": skill_id,
        "agent_id": agent_id,
        "user_id": user_id,
        "elapsed_seconds": round(elapsed, 1),
        "turns": turn_records,
    }


# -----------------------------------------------------------------------------
# Phase 2 — QA (actual skill usage)
# -----------------------------------------------------------------------------

async def run_skill_qa(skill_entry: dict, bench_config: Any) -> dict:
    """Probe the installed skill by asking it to actually do things."""
    from xyz_agent_context.agent_runtime import AgentRuntime
    from xyz_agent_context.schema import WorkingSource, ProgressMessage

    skill_id = skill_entry["id"]
    agent_id = _agent_id_from_skill(skill_id)
    user_id = USER_ID_PREFIX
    questions: list[str] = skill_entry.get("qa_questions", [])

    print(f"\n----- Phase 2 QA: {skill_id} -----")
    print(f"  questions={len(questions)}")

    skip_modules = bench_config.qa_skip_modules() if bench_config else set()
    skip_narrative = "Narrative" in skip_modules

    results: list[dict] = []
    for idx, q in enumerate(questions, start=1):
        print(f"  [{idx}/{len(questions)}] Q: {q[:80]}")
        agent_answer = ""
        tool_uses: list[str] = []
        t0 = time.monotonic()
        try:
            async with AgentRuntime() as runtime:
                async for msg in runtime.run(
                    agent_id=agent_id,
                    user_id=user_id,
                    input_content=q,
                    working_source=WorkingSource.CHAT,
                    read_only=True,
                    skip_modules=skip_modules,
                    skip_narrative_prompt=skip_narrative,
                ):
                    if isinstance(msg, ProgressMessage):
                        tn = (getattr(msg, "details", None) or {}).get("tool_name", "")
                        if tn:
                            tool_uses.append(tn)
                        if tn.endswith("send_message_to_user_directly"):
                            c = msg.details.get("arguments", {}).get("content", "")
                            if c:
                                agent_answer += c
        except Exception as exc:
            agent_answer = f"[ERROR] {exc}"
        elapsed = time.monotonic() - t0
        status = "OK" if agent_answer and not agent_answer.startswith("[ERROR]") else "EMPTY_OR_ERR"
        print(f"       A: {agent_answer[:120]}")
        print(f"       ({elapsed:.1f}s, {len(tool_uses)} tool calls, status={status})")
        results.append({
            "question": q,
            "agent_answer": agent_answer,
            "elapsed_seconds": round(elapsed, 1),
            "status": status,
            "tool_calls": tool_uses,
        })
    return {"skill_id": skill_id, "agent_id": _agent_id_from_skill(skill_id), "qa_results": results}


# -----------------------------------------------------------------------------
# Phase 3 — Cleanup
# -----------------------------------------------------------------------------

def run_cleanup(skill_entry: dict, *, dry_run: bool = False) -> dict:
    """Wipe workspace + cancel jobs for this agent. Imports the cleanup script
    in-process so we get programmatic results."""
    import importlib.util
    spec = importlib.util.spec_from_file_location("cleanup_skill_state",
                                                  _ROOT / "scripts" / "cleanup_skill_state.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    agent_id = _agent_id_from_skill(skill_entry["id"])
    workspace = mod.WORKSPACE_BASE / agent_id

    print(f"\n----- Phase 3 Cleanup: {skill_entry['id']} -----")
    fs = mod.cleanup_filesystem(workspace, dry_run=dry_run)
    print(f"  filesystem: removed_skills={fs['removed_skills']}, workspace_removed={fs['removed_workspace']}")

    jobs = mod.cleanup_jobs([agent_id], dry_run=dry_run)
    print(f"  jobs cancelled: {jobs['cancelled']}")

    instances = mod.cleanup_module_instances([agent_id], dry_run=dry_run)
    print(f"  module_instances removed: {instances['removed']}")

    return {"agent_id": agent_id, "filesystem": fs, "jobs": jobs, "instances": instances}


# -----------------------------------------------------------------------------
# Orchestration
# -----------------------------------------------------------------------------

async def run_one_skill(entry: dict, bench_config: Any, *, results_dir: Path, no_cleanup: bool) -> dict:
    skill_id = entry["id"]
    out_dir = results_dir / f"run_{skill_id}"
    out_dir.mkdir(parents=True, exist_ok=True)

    dump_dir = str(out_dir / "dumps")
    Path(dump_dir).mkdir(parents=True, exist_ok=True)

    overall_t0 = time.monotonic()
    record = {"skill_id": skill_id, "phases": {}}

    try:
        record["phases"]["replay"] = await run_install_replay(entry, dump_dir=dump_dir)
        record["phases"]["qa"] = await run_skill_qa(entry, bench_config)
    finally:
        if no_cleanup:
            print(f"\n----- Phase 3 SKIPPED (--no-cleanup) for {skill_id} -----")
            record["phases"]["cleanup"] = {"skipped": True}
        else:
            record["phases"]["cleanup"] = run_cleanup(entry)

    record["elapsed_total_seconds"] = round(time.monotonic() - overall_t0, 1)

    out_file = out_dir / "run_record.json"
    out_file.write_text(json.dumps(record, indent=2, ensure_ascii=False))
    print(f"\n  -> {out_file} (total {record['elapsed_total_seconds']}s)")
    return record


async def main_async(args: argparse.Namespace) -> None:
    from benchmark.replay.test_config import BenchmarkConfig

    catalog = yaml.safe_load(Path(args.catalog).read_text("utf-8"))
    skills = catalog if isinstance(catalog, list) else catalog.get("skills", [])
    if args.skill_id:
        skills = [s for s in skills if s["id"] == args.skill_id]
    if not skills:
        print("No skills selected; check --catalog and --skill-id.")
        sys.exit(1)

    config_path = args.config or "benchmark/test_configs/skill_isolation.yaml"
    bench_config = BenchmarkConfig.from_yaml(config_path)
    print(f"Loaded BenchmarkConfig from {config_path}")
    print(f"Running {len(skills)} skill(s): {[s['id'] for s in skills]}")

    results_dir = Path(args.results_dir)
    results_dir.mkdir(parents=True, exist_ok=True)

    summary: list[dict] = []
    for entry in skills:
        record = await run_one_skill(entry, bench_config, results_dir=results_dir,
                                     no_cleanup=args.no_cleanup)
        summary.append({"skill_id": record["skill_id"],
                        "elapsed_total_seconds": record["elapsed_total_seconds"]})

    (results_dir / "summary.json").write_text(json.dumps(summary, indent=2))
    print(f"\n=== Summary written to {results_dir / 'summary.json'} ===")


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--catalog", required=True, help="YAML catalog file (list of skill entries).")
    p.add_argument("--skill-id", help="Run only this skill_id from the catalog.")
    p.add_argument("--config", help="Test config YAML; default: benchmark/test_configs/skill_isolation.yaml")
    p.add_argument("--results-dir", required=True, help="Output directory.")
    p.add_argument("--no-cleanup", action="store_true",
                   help="Skip Phase 3. Useful for debugging — leaves the workspace for inspection.")
    args = p.parse_args()
    asyncio.run(main_async(args))


if __name__ == "__main__":
    main()
