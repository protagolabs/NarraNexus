"""
Shared primitives for building NarraNexus .nxbundle files from external sources.

Used by:
    convert_single.py   — one SOUL.md  -> single-agent bundle
    convert_team.py     — N SOUL.md    -> team bundle
    auto_team_detect.py — scan SOUL.md cross-refs for team candidates

Importer minimums (verified 2026-05-28 from bundle/importer.py):
    Required: manifest.json + agents/<id>/agent.json
    Functional: + awareness.json
    Optional (.exists() gated): everything else
"""

from __future__ import annotations

import hashlib
import io
import json
import random
import re
import string
import tarfile
import zipfile
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable


OWNER_PLACEHOLDER = "<original_owner>"

# ---------------------------------------------------------------------------
# Rebrand pass (OpenClaw -> NarraNexus)
# ---------------------------------------------------------------------------

REBRAND_RULES = [
    (re.compile(r"powered by OpenClaw", re.IGNORECASE), "powered by NarraNexus"),
    (re.compile(r"the OpenClaw way", re.IGNORECASE), "the NarraNexus way"),
    (re.compile(r"OpenClaw gateway", re.IGNORECASE), "NarraNexus gateway"),
    (re.compile(r"OpenClaw heartbeat", re.IGNORECASE), "NarraNexus heartbeat"),
    (re.compile(r"OpenClaw\b", re.IGNORECASE), "NarraNexus"),
]


def rebrand(text: str) -> tuple[str, list[str]]:
    """Stage A regex rebrand. Returns (rewritten, diffs_applied)."""
    diffs = []
    out = text
    for pattern, repl in REBRAND_RULES:
        new = pattern.sub(repl, out)
        if new != out:
            diffs.append(f"{pattern.pattern} -> {repl}")
            out = new
    return out, diffs


# ---------------------------------------------------------------------------
# IDs + timestamps
# ---------------------------------------------------------------------------

def gen_id(prefix: str, length: int = 12) -> str:
    return f"{prefix}_{''.join(random.choices('0123456789abcdef', k=length))}"


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# ---------------------------------------------------------------------------
# Instance stamps (5 modules — minimum viable for a working agent)
# ---------------------------------------------------------------------------

def _stamp(*, module_class: str, agent_id: str, owner: str, instance_id: str,
           keywords: list, topic_hint: str, description: str) -> dict:
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


def make_module_stamps(agent_id: str, owner: str = OWNER_PLACEHOLDER) -> dict:
    """Return {module_class: [instance_stamp, ...]} for the 5 default modules."""
    return {
        "AwarenessModule": [_stamp(
            module_class="AwarenessModule", agent_id=agent_id, owner=owner,
            instance_id=gen_id("aware", 8),
            keywords=["awareness", "identity", "behavior"],
            topic_hint="Agent identity, behavioral profile, capabilities",
            description=f"Awareness instance for agent {agent_id}",
        )],
        "BasicInfoModule": [_stamp(
            module_class="BasicInfoModule", agent_id=agent_id, owner=owner,
            instance_id=gen_id("basic", 8),
            keywords=["info", "metadata", "profile"],
            topic_hint="Basic agent metadata",
            description=f"Basic info instance for agent {agent_id}",
        )],
        "ChatModule": [_stamp(
            module_class="ChatModule", agent_id=agent_id, owner=owner,
            instance_id=gen_id("chat", 8),
            keywords=["chat", "conversation", "dialogue"],
            topic_hint="Chat interactions and message history",
            description=f"Chat instance for user {owner}",
        )],
        "SocialNetworkModule": [_stamp(
            module_class="SocialNetworkModule", agent_id=agent_id, owner=owner,
            instance_id=gen_id("social", 8),
            keywords=["social", "entities", "graph"],
            topic_hint="Social network graph",
            description=f"Social network instance for agent {agent_id}",
        )],
        "MessageBusModule": [_stamp(
            module_class="MessageBusModule", agent_id=agent_id, owner=owner,
            instance_id=gen_id("bus", 8),
            keywords=["messagebus", "channels", "inbox"],
            topic_hint="MessageBus channels and inbox",
            description=f"MessageBus instance for agent {agent_id}",
        )],
    }


# ---------------------------------------------------------------------------
# Workspace packing — skills go into workspace.tar.gz under skills/<name>/
# ---------------------------------------------------------------------------

@dataclass
class SkillSpec:
    """A skill to bundle. ``src_dir`` is a local path like
    ``/tmp/awesome-openclaw-agents/skills/claude/git-commit-writer``,
    containing SKILL.md + optional scripts/, references/, etc."""
    src_dir: Path
    name: str  # the skill folder name in workspace (also used in manifest)
    apply_rebrand: bool = True  # rebrand SKILL.md text-content files


def _is_text_file(name: str) -> bool:
    return name.endswith((".md", ".txt", ".json", ".yaml", ".yml", ".sh", ".py", ".js", ".mjs", ".ts"))


def build_workspace_tar(skills: list[SkillSpec]) -> tuple[bytes, list[dict]]:
    """Pack a list of skills into a workspace.tar.gz buffer.

    Returns (tar_bytes, skill_manifest_entries).
    skill_manifest_entries is a list of dicts suitable for manifest.skills.
    """
    buf = io.BytesIO()
    manifest_entries: list[dict] = []

    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        if not skills:
            # placeholder so tar isn't empty
            info = tarfile.TarInfo(name=".keep")
            info.size = 0
            tar.addfile(info, io.BytesIO(b""))
        else:
            for skill in skills:
                if not skill.src_dir.is_dir():
                    raise FileNotFoundError(f"skill src_dir not found: {skill.src_dir}")
                # Walk skill dir, add each file under skills/<name>/...
                files_added = []
                for f in sorted(skill.src_dir.rglob("*")):
                    if not f.is_file():
                        continue
                    rel = f.relative_to(skill.src_dir).as_posix()
                    arcname = f"skills/{skill.name}/{rel}"
                    if skill.apply_rebrand and _is_text_file(f.name):
                        raw = f.read_text(encoding="utf-8", errors="replace")
                        rewritten, _ = rebrand(raw)
                        data = rewritten.encode("utf-8")
                        info = tarfile.TarInfo(name=arcname)
                        info.size = len(data)
                        info.mtime = int(datetime.now(timezone.utc).timestamp())
                        tar.addfile(info, io.BytesIO(data))
                    else:
                        tar.add(f, arcname=arcname)
                    files_added.append(rel)

                # sha256 of the SKILL.md (canonical identity of the skill)
                skill_md = skill.src_dir / "SKILL.md"
                if skill_md.is_file():
                    raw = skill_md.read_text(encoding="utf-8", errors="replace")
                    rewritten, _ = (rebrand(raw) if skill.apply_rebrand else (raw, []))
                    sha = hashlib.sha256(rewritten.encode("utf-8")).hexdigest()
                else:
                    sha = ""

                manifest_entries.append({
                    "name": skill.name,
                    "skill_dir": skill.name,
                    "install_method": "bundled",  # bundled inside workspace.tar.gz
                    "contains_secrets": False,
                    "sha256": sha,
                    "files": files_added,
                })

    return buf.getvalue(), manifest_entries


# ---------------------------------------------------------------------------
# Per-agent files
# ---------------------------------------------------------------------------

@dataclass
class AgentSpec:
    name: str
    role: str
    category: str
    soul_md_text: str  # raw markdown body
    source_path: str   # e.g. "agents/productivity/orion/SOUL.md"
    skills: list[SkillSpec] = field(default_factory=list)
    source_repo: str = "github:mergisi/awesome-openclaw-agents"
    source_license: str = "MIT"


@dataclass
class BuiltAgent:
    """Outputs for a single agent — to be assembled into a bundle."""
    agent_id: str
    agent_name: str
    agent_json: dict
    awareness_json: list
    instance_stamps: dict
    workspace_tar: bytes
    skill_entries: list[dict]
    rebrand_diffs: list[str]


def build_agent_files(spec: AgentSpec) -> BuiltAgent:
    agent_id = gen_id("agent", 12)
    rebranded, rebrand_diffs = rebrand(spec.soul_md_text)

    instance_stamps = make_module_stamps(agent_id)
    awareness_iid = instance_stamps["AwarenessModule"][0]["instance_id"]

    agent_json = {
        "id": 1,
        "agent_id": agent_id,
        "agent_name": spec.name,
        "created_by": OWNER_PLACEHOLDER,
        "agent_description": spec.role or f"Imported from {spec.source_repo}",
        "agent_type": "chat",
        "is_public": 0,
        "agent_metadata": json.dumps({
            "category": spec.category,
            "source": {
                "repo": spec.source_repo,
                "path": spec.source_path,
                "license": spec.source_license,
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

    workspace_tar, skill_entries = build_workspace_tar(spec.skills)
    # Tag manifest skill entries with this agent's id (matches export format)
    for e in skill_entries:
        e["agent_id"] = agent_id

    return BuiltAgent(
        agent_id=agent_id,
        agent_name=spec.name,
        agent_json=agent_json,
        awareness_json=awareness_json,
        instance_stamps=instance_stamps,
        workspace_tar=workspace_tar,
        skill_entries=skill_entries,
        rebrand_diffs=rebrand_diffs,
    )


# ---------------------------------------------------------------------------
# Bundle assembly
# ---------------------------------------------------------------------------

@dataclass
class TeamMeta:
    name: str
    description: str
    color: str = "#3b82f6"   # default blue
    intro_md: str = ""


def write_bundle(
    *,
    out_path: Path,
    agents: list[BuiltAgent],
    team: TeamMeta | None = None,
    bundle_info_extra: list[str] | None = None,
) -> dict:
    """Assemble a .nxbundle ZIP from one or more BuiltAgent rows.

    - team=None  -> single-agent bundle (manifest.team=null)
    - team=...   -> multi-agent bundle (manifest.team populated)
    """
    if not agents:
        raise ValueError("at least one agent required")

    team_id = gen_id("team", 12) if team else None
    agents_summary = []
    skills_index: list[dict] = []
    for a in agents:
        agents_summary.append({
            "agent_id": a.agent_id,
            "agent_name": a.agent_name,
            "narratives": 0,
            "instances": sum(len(v) for v in a.instance_stamps.values()),
            "social_entities": 0,
            "rag_rows": 0,
            "artifacts": 0,
            "workspace_size_bytes": len(a.workspace_tar),
            "workspace_path": "workspace.tar.gz",
        })
        skills_index.extend(a.skill_entries)

    manifest = {
        "bundle_format_version": "1.1",
        "narranexus_version_exported": "1.7.2",
        "exported_at": now_iso(),
        "owner_placeholder": OWNER_PLACEHOLDER,
        "team": {
            "team_id": team_id,
            "name": team.name,
            "description": team.description,
            "color": team.color,
            "source": "bundle",
            "intro_md": team.intro_md,
        } if team else None,
        "agents": [a.agent_id for a in agents],
        "agents_summary": agents_summary,
        "skills": skills_index,
        "mcp_hints_count": 0,
        "artifacts_count": 0,
        "stripped": ["api_keys", "lark_oauth", "user_password_hash", "user_providers"],
        "warnings": [],
        "info": [
            "Template generated from OpenClaw SOUL.md sources",
            f"Agents: {', '.join(a.agent_name for a in agents)}",
            f"Skills bundled: {len(skills_index)}",
            "Source platform: OpenClaw (rebranded to NarraNexus)",
            *(bundle_info_extra or []),
        ],
        "info_counters": {},
        "embedding": None,
        "source_attribution": [
            {
                "agent_id": a.agent_id,
                "agent_name": a.agent_name,
                "source": json.loads(a.agent_json["agent_metadata"])["source"],
                "rebrand_diffs": a.rebrand_diffs,
            } for a in agents
        ],
    }

    with zipfile.ZipFile(out_path, mode="w", compression=zipfile.ZIP_DEFLATED) as z:
        z.writestr("manifest.json", json.dumps(manifest, indent=2))
        z.writestr("bus.json", json.dumps({
            "channels": [], "members": [], "messages": [], "registry": []
        }))
        z.writestr("inbox.json", "[]")
        z.writestr("mcp_hints.json", "[]")

        for a in agents:
            agent_dir = f"agents/{a.agent_id}"
            z.writestr(f"{agent_dir}/agent.json", json.dumps(a.agent_json, indent=2))
            z.writestr(f"{agent_dir}/awareness.json", json.dumps(a.awareness_json, indent=2))
            z.writestr(f"{agent_dir}/workspace.tar.gz", a.workspace_tar)
            for module_class, stamps in a.instance_stamps.items():
                for stamp in stamps:
                    z.writestr(
                        f"{agent_dir}/instances/{module_class}/{stamp['instance_id']}.json",
                        json.dumps(stamp, indent=2),
                    )

    return {
        "out_path": str(out_path),
        "bundle_size_bytes": out_path.stat().st_size,
        "agent_count": len(agents),
        "agents": [{"id": a.agent_id, "name": a.agent_name,
                    "skills": len(a.skill_entries),
                    "awareness_chars": len(a.awareness_json[0]["awareness"])}
                   for a in agents],
        "skill_count_total": len(skills_index),
        "team_id": team_id,
    }


# ---------------------------------------------------------------------------
# Convenience: load SOUL.md text from a path
# ---------------------------------------------------------------------------

def load_soul(path: Path) -> str:
    return Path(path).read_text(encoding="utf-8")
