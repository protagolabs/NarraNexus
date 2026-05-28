#!/usr/bin/env python3
"""
POC: Convert one OpenClaw SOUL.md -> NarraNexus .nxbundle.

Pipeline:
    1. Read SOUL.md (pure markdown, no frontmatter)
    2. Rebrand pass: OpenClaw -> NarraNexus (regex stage; LLM stage stubbed)
    3. Emit minimum-viable NarraNexus bundle:
         - manifest.json
         - bus.json / inbox.json / mcp_hints.json (empty)
         - agents/<id>/agent.json
         - agents/<id>/awareness.json  (rebranded body)
         - agents/<id>/instances/<Module>/<id>.json  (5 module stamps)
         - agents/<id>/workspace.tar.gz  (placeholder, skills attached in next phase)
    4. ZIP into .nxbundle

Verified importer minimums (read 2026-05-22 from bundle/importer.py):
    Required: manifest.json + agents/<id>/agent.json
    Functional: + awareness.json (line 750-760, instance_awareness table)
    Optional (all .exists() gated): narratives, instances, agent_messages,
        jobs, artifacts, rag, bus, mcp_hints, workspace.tar.gz

Usage:
    python convert_soul_to_nxbundle.py \\
        --soul path/to/SOUL.md \\
        --name "Orion" \\
        --role "Task coordinator and project orchestrator" \\
        --category productivity \\
        --source-path agents/productivity/orion/SOUL.md \\
        --out orion.nxbundle
"""

import argparse
import io
import json
import random
import re
import string
import tarfile
import zipfile
from datetime import datetime, timezone
from pathlib import Path


# ---------------------------------------------------------------------------
# Rebrand pass
# ---------------------------------------------------------------------------

REBRAND_RULES = [
    # Order matters: most specific first.
    (re.compile(r"powered by OpenClaw", re.IGNORECASE), "powered by NarraNexus"),
    (re.compile(r"the OpenClaw way", re.IGNORECASE), "the NarraNexus way"),
    (re.compile(r"OpenClaw gateway", re.IGNORECASE), "NarraNexus gateway"),
    (re.compile(r"OpenClaw heartbeat", re.IGNORECASE), "NarraNexus heartbeat"),
    (re.compile(r"OpenClaw\b", re.IGNORECASE), "NarraNexus"),
]


def regex_rebrand(text: str) -> tuple[str, list[str]]:
    """Mechanical pre-pass. Returns (rewritten_text, list_of_diffs_seen)."""
    diffs = []
    out = text
    for pattern, repl in REBRAND_RULES:
        new = pattern.sub(repl, out)
        if new != out:
            diffs.append(f"{pattern.pattern} -> {repl}")
            out = new
    return out, diffs


def llm_rebrand_hook(text: str) -> str:
    """LLM rewrite stage. Stubbed for POC.

    Wire-up plan:
      - Call Claude (Anthropic SDK) with a structured prompt:
          "Rewrite this agent system prompt. Replace any OpenClaw platform
           references with NarraNexus equivalents where natural. Preserve
           agent identity, capabilities, behavioral guidelines verbatim.
           Preserve peer-agent name references (Echo, Radar, etc.) -- those
           are separate co-agents we also import. Output rewritten content
           only, no commentary."
      - Cache responses keyed by input hash to avoid re-spending.
      - Quality gate: diff regex-only vs LLM output, flag for human review
        if LLM removed > N% of content.

    Until live: returns text unchanged (regex pass handled mechanical part).
    """
    return text


def rebrand(text: str, *, use_llm: bool = False) -> tuple[str, list[str]]:
    out, diffs = regex_rebrand(text)
    if use_llm:
        out = llm_rebrand_hook(out)
    return out, diffs


# ---------------------------------------------------------------------------
# ID generation (mirrors NarraNexus convention: prefix + N hex chars)
# ---------------------------------------------------------------------------

def gen_id(prefix: str, length: int = 12) -> str:
    return f"{prefix}_{''.join(random.choices('0123456789abcdef', k=length))}"


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# ---------------------------------------------------------------------------
# Instance stamps
# ---------------------------------------------------------------------------

def make_instance_stamp(
    *,
    module_class: str,
    agent_id: str,
    owner: str,
    instance_id: str,
    keywords: list,
    topic_hint: str,
    description: str,
) -> dict:
    """Minimum viable instance row, matching schema_registry shape."""
    return {
        "instance_id": instance_id,
        "module_class": module_class,
        "agent_id": agent_id,
        "user_id": owner,
        "is_public": 0,
        "status": "active",
        "description": description,
        "dependencies": "[]",
        "config": "{}",
        "state": None,
        "routing_embedding": None,
        "keywords": json.dumps(keywords),
        "topic_hint": topic_hint,
        "last_used_at": None,
        "completed_at": None,
        "archived_at": None,
        "last_polled_status": None,
        "callback_processed": 0,
        "created_at": now_iso(),
        "updated_at": now_iso(),
    }


def make_module_stamps(agent_id: str, owner: str) -> dict:
    """Return {module_class: [instance_stamp, ...]} for the 5 default modules."""
    return {
        "AwarenessModule": [
            make_instance_stamp(
                module_class="AwarenessModule", agent_id=agent_id, owner=owner,
                instance_id=gen_id("aware", 8),
                keywords=["awareness", "identity", "behavior"],
                topic_hint="Agent identity, behavioral profile, capabilities",
                description=f"Awareness instance for agent {agent_id}",
            )
        ],
        "BasicInfoModule": [
            make_instance_stamp(
                module_class="BasicInfoModule", agent_id=agent_id, owner=owner,
                instance_id=gen_id("basic", 8),
                keywords=["info", "metadata", "profile"],
                topic_hint="Basic agent metadata",
                description=f"Basic info instance for agent {agent_id}",
            )
        ],
        "ChatModule": [
            make_instance_stamp(
                module_class="ChatModule", agent_id=agent_id, owner=owner,
                instance_id=gen_id("chat", 8),
                keywords=["chat", "conversation", "dialogue"],
                topic_hint="Chat interactions and message history",
                description=f"Chat instance for user {owner}",
            )
        ],
        "SocialNetworkModule": [
            make_instance_stamp(
                module_class="SocialNetworkModule", agent_id=agent_id, owner=owner,
                instance_id=gen_id("social", 8),
                keywords=["social", "entities", "graph"],
                topic_hint="Social network graph",
                description=f"Social network instance for agent {agent_id}",
            )
        ],
        "MessageBusModule": [
            make_instance_stamp(
                module_class="MessageBusModule", agent_id=agent_id, owner=owner,
                instance_id=gen_id("bus", 8),
                keywords=["messagebus", "channels", "inbox"],
                topic_hint="MessageBus channels and inbox",
                description=f"MessageBus instance for agent {agent_id}",
            )
        ],
    }


# ---------------------------------------------------------------------------
# Bundle assembly
# ---------------------------------------------------------------------------

OWNER_PLACEHOLDER = "<original_owner>"


def build_bundle(
    *,
    soul_text: str,
    name: str,
    role: str,
    category: str,
    source_repo: str,
    source_path: str,
    source_license: str,
    out_path: Path,
    use_llm_rebrand: bool = False,
) -> dict:
    agent_id = gen_id("agent", 12)
    rebranded, rebrand_diffs = rebrand(soul_text, use_llm=use_llm_rebrand)

    instance_stamps = make_module_stamps(agent_id, OWNER_PLACEHOLDER)
    awareness_iid = instance_stamps["AwarenessModule"][0]["instance_id"]

    agent_json = {
        "id": 1,
        "agent_id": agent_id,
        "agent_name": name,
        "created_by": OWNER_PLACEHOLDER,
        "agent_description": role or f"Imported from {source_repo}",
        "agent_type": "chat",
        "is_public": 0,
        "agent_metadata": json.dumps({
            "category": category,
            "source": {
                "repo": source_repo,
                "path": source_path,
                "license": source_license,
            },
            "rebrand_applied": True,
            "rebrand_diffs": rebrand_diffs,
        }),
        "agent_create_time": now_iso(),
        "agent_update_time": now_iso(),
    }

    awareness_json = [{
        "id": 1,
        "instance_id": awareness_iid,
        "awareness": rebranded,
        "created_at": now_iso(),
        "updated_at": now_iso(),
    }]

    # Placeholder workspace.tar.gz (skills attached in Phase 2)
    ws_buf = io.BytesIO()
    with tarfile.open(fileobj=ws_buf, mode="w:gz") as tar:
        info = tarfile.TarInfo(name=".keep")
        info.size = 0
        tar.addfile(info, io.BytesIO(b""))
    workspace_bytes = ws_buf.getvalue()

    total_instances = sum(len(v) for v in instance_stamps.values())
    manifest = {
        "bundle_format_version": "1.1",
        "narranexus_version_exported": "1.7.2",
        "exported_at": now_iso(),
        "owner_placeholder": OWNER_PLACEHOLDER,
        "team": None,
        "agents": [agent_id],
        "agents_summary": [{
            "agent_id": agent_id,
            "agent_name": name,
            "narratives": 0,
            "instances": total_instances,
            "social_entities": 0,
            "rag_rows": 0,
            "artifacts": 0,
            "workspace_size_bytes": len(workspace_bytes),
            "workspace_path": "workspace.tar.gz",
        }],
        "skills": [],
        "mcp_hints_count": 0,
        "artifacts_count": 0,
        "stripped": ["api_keys", "lark_oauth", "user_password_hash", "user_providers"],
        "warnings": [],
        "info": [
            f"Template generated from {source_repo}:{source_path}",
            f"License: {source_license}",
            "Source platform: OpenClaw (rebranded to NarraNexus)",
        ],
        "info_counters": {},
        "embedding": None,
        "source_attribution": {
            "repo": source_repo,
            "path": source_path,
            "license": source_license,
            "rebrand_applied": True,
            "rebrand_diffs": rebrand_diffs,
        },
    }

    # Write the ZIP
    with zipfile.ZipFile(out_path, mode="w", compression=zipfile.ZIP_DEFLATED) as z:
        z.writestr("manifest.json", json.dumps(manifest, indent=2))
        z.writestr("bus.json", json.dumps({
            "channels": [], "members": [], "messages": [], "registry": []
        }))
        z.writestr("inbox.json", "[]")
        z.writestr("mcp_hints.json", "[]")

        agent_dir = f"agents/{agent_id}"
        z.writestr(f"{agent_dir}/agent.json", json.dumps(agent_json, indent=2))
        z.writestr(f"{agent_dir}/awareness.json", json.dumps(awareness_json, indent=2))
        z.writestr(f"{agent_dir}/workspace.tar.gz", workspace_bytes)
        for module_class, stamps in instance_stamps.items():
            for stamp in stamps:
                z.writestr(
                    f"{agent_dir}/instances/{module_class}/{stamp['instance_id']}.json",
                    json.dumps(stamp, indent=2),
                )

    return {
        "agent_id": agent_id,
        "awareness_chars": len(rebranded),
        "rebrand_diff_chars": len(rebranded) - len(soul_text),
        "rebrand_rules_applied": rebrand_diffs,
        "instance_count": total_instances,
        "out_path": str(out_path),
        "bundle_size_bytes": out_path.stat().st_size,
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--soul", required=True, help="Path to SOUL.md")
    ap.add_argument("--name", required=True)
    ap.add_argument("--role", default="")
    ap.add_argument("--category", required=True)
    ap.add_argument("--source-repo", default="github:mergisi/awesome-openclaw-agents")
    ap.add_argument("--source-path", required=True)
    ap.add_argument("--source-license", default="MIT")
    ap.add_argument("--use-llm-rebrand", action="store_true",
                    help="Run the LLM rebrand hook (stubbed; no-op until SDK wired)")
    ap.add_argument("--out", required=True, help="Output .nxbundle path")
    args = ap.parse_args()

    soul_text = Path(args.soul).read_text(encoding="utf-8")
    result = build_bundle(
        soul_text=soul_text,
        name=args.name,
        role=args.role,
        category=args.category,
        source_repo=args.source_repo,
        source_path=args.source_path,
        source_license=args.source_license,
        out_path=Path(args.out),
        use_llm_rebrand=args.use_llm_rebrand,
    )
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
