"""
@file_name: builder.py
@author: NetMind.AI
@date: 2026-05-08
@description: Bundle export builder — serialize closure of selected agents into a .nxbundle zip

Pipeline (PRD §8.3 + §8.12):
1. Closure: only the selected agent_ids are exported
2. Drop external references (records pointing to agents NOT in closure)
3. Strip credentials (lark_credentials, user_providers, password_hash never leave)
4. Replace user_id with placeholder
5. Serialize 25 tables → manifest + per-agent dirs
6. Skill bundling: URL / Zip / Full Copy per skill
7. Workspace tar.gz with sensitive-pattern filter
8. Compute integrity sha256, zip into .nxbundle
"""

import asyncio
import io
import json
import os
import shutil
import tarfile
import tempfile
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Set
from loguru import logger

from xyz_agent_context.utils.db_factory import get_db_client
from .security import (
    bytes_sha256,
    file_sha256,
    is_sensitive_path,
    is_volume_path,
    scan_zip_for_sensitive,
)


BUNDLE_FORMAT_VERSION = "1.0"


# Tables that store per-agent state (closure-filtered).
AGENT_SCOPED_TABLES = [
    "agents",
    "events",
    "narratives",
    "agent_messages",
    "module_instances",
    "instance_jobs",
    "module_report_memory",
    "lark_trigger_audit",
]

# Tables keyed by instance_id (filter via instance closure).
INSTANCE_SCOPED_TABLES = [
    "instance_social_entities",
    "instance_rag_store",
    "instance_narrative_links",
    "instance_awareness",
    "instance_module_report_memory",
    "instance_json_format_memory",
    "instance_json_format_memory_chat",
]

# Tables to skip (credentials).
STRIPPED_TABLES = {
    "lark_credentials",
    "user_providers",
    "user_slots",
    "user_quotas",
    "users",
    "user_password_hash",
    "lark_seen_messages",
    "lark_trigger_audit",  # contains user/sender ids
}


class ExportSelection:
    """Agent-scoped selection for what to include in the bundle."""

    def __init__(
        self,
        agent_ids: List[str],
        team_id: Optional[str] = None,
        team_intro_md: Optional[str] = None,
        skill_methods: Optional[Dict[str, Dict[str, Any]]] = None,
        social_entity_selection: Optional[Dict[str, List[str]]] = None,
        workspace_excludes: Optional[Dict[str, List[str]]] = None,
        include_chat_history: bool = True,
        embedding_provider: Optional[str] = None,
        embedding_model: Optional[str] = None,
        embedding_dim: Optional[int] = None,
    ):
        self.agent_ids = agent_ids
        self.team_id = team_id
        self.team_intro_md = team_intro_md or ""
        # skill_methods: { skill_name: {"install_method": "url"|"zip"|"full_copy",
        #                                "source_url": ..., "manual_zip_path": ...} }
        self.skill_methods = skill_methods or {}
        # social_entity_selection: { agent_id: [entity_id, ...] }
        # If None, default to: per-agent, all entities matching (team-name fuzzy).
        # If provided, use the given list verbatim.
        self.social_entity_selection = social_entity_selection
        # workspace_excludes: { agent_id: [relative_path, ...] }  (manual user de-checks)
        self.workspace_excludes = workspace_excludes or {}
        self.include_chat_history = include_chat_history
        self.embedding_provider = embedding_provider
        self.embedding_model = embedding_model
        self.embedding_dim = embedding_dim


async def build_bundle(
    user_id: str,
    selection: ExportSelection,
    output_path: Path,
) -> Dict[str, Any]:
    """Build a .nxbundle file at output_path.
    Returns a manifest summary dict."""
    db = await get_db_client()

    closure_set: Set[str] = set(selection.agent_ids)
    if not closure_set:
        raise ValueError("agent_ids must not be empty")

    # 1. Validate: every agent must be owned by this user
    for aid in closure_set:
        row = await db.get_one("agents", {"agent_id": aid})
        if not row:
            raise ValueError(f"Agent not found: {aid}")
        if row["created_by"] != user_id:
            raise ValueError(f"Forbidden: agent {aid} not owned by {user_id}")

    warnings: List[str] = []
    stripped_lists = ["api_keys", "lark_oauth", "user_password_hash", "user_providers"]

    # 2. Find all instance_ids belonging to closure agents
    instance_ids: Set[str] = set()
    for aid in closure_set:
        rows = await db.get("module_instances", {"agent_id": aid})
        for r in rows:
            instance_ids.add(r["instance_id"])

    # 3. Build a temp staging dir
    with tempfile.TemporaryDirectory() as tmpdir_str:
        tmpdir = Path(tmpdir_str)

        # ---- per-agent directories ----
        agents_summary: List[Dict[str, Any]] = []

        for aid in closure_set:
            agent_dir = tmpdir / "agents" / aid
            agent_dir.mkdir(parents=True, exist_ok=True)

            # agent.json
            agent_row = await db.get_one("agents", {"agent_id": aid})
            agent_record = _scrub_user_id(dict(agent_row), user_id)
            (agent_dir / "agent.json").write_text(
                json.dumps(agent_record, indent=2, ensure_ascii=False, default=str),
                encoding="utf-8",
            )

            # awareness — collected from instance_awareness rows
            awareness_rows = []
            for iid in instance_ids:
                rows = await db.get(
                    "instance_awareness",
                    {"instance_id": iid},
                )
                for r in rows:
                    inst = await db.get_one("module_instances", {"instance_id": iid})
                    if inst and inst.get("agent_id") == aid:
                        awareness_rows.append(r)
            (agent_dir / "awareness.json").write_text(
                json.dumps([_scrub_user_id(dict(r), user_id) for r in awareness_rows],
                           indent=2, ensure_ascii=False, default=str),
                encoding="utf-8",
            )

            # narratives + events
            narratives_dir = agent_dir / "narratives"
            narratives_dir.mkdir(parents=True, exist_ok=True)
            n_rows = await db.get("narratives", {"agent_id": aid})
            for n in n_rows:
                ndir = narratives_dir / n["narrative_id"]
                ndir.mkdir(parents=True, exist_ok=True)
                (ndir / "narrative.json").write_text(
                    json.dumps(_scrub_user_id(dict(n), user_id),
                               indent=2, ensure_ascii=False, default=str),
                    encoding="utf-8",
                )
                if selection.include_chat_history:
                    e_rows = await db.get(
                        "events",
                        {"narrative_id": n["narrative_id"]},
                        order_by="created_at ASC",
                    )
                    e_path = ndir / "events.jsonl"
                    with open(e_path, "w", encoding="utf-8") as f:
                        for e in e_rows:
                            scrubbed = _scrub_user_id(dict(e), user_id)
                            f.write(json.dumps(scrubbed, ensure_ascii=False, default=str) + "\n")

            # module_instances + instance-scoped tables (filtered to this agent's instances)
            instances_dir = agent_dir / "instances"
            instances_dir.mkdir(parents=True, exist_ok=True)
            agent_instance_ids = []
            inst_rows = await db.get("module_instances", {"agent_id": aid})
            for r in inst_rows:
                agent_instance_ids.append(r["instance_id"])
                klass = r["module_class"]
                kdir = instances_dir / klass
                kdir.mkdir(parents=True, exist_ok=True)
                (kdir / f"{r['instance_id']}.json").write_text(
                    json.dumps(_scrub_user_id(dict(r), user_id),
                               indent=2, ensure_ascii=False, default=str),
                    encoding="utf-8",
                )

            # Per-agent social entities (subject to social_entity_selection)
            social_entities_for_agent = []
            for iid in agent_instance_ids:
                rows = await db.get("instance_social_entities", {"instance_id": iid})
                if selection.social_entity_selection is not None:
                    selected_ids = set(selection.social_entity_selection.get(aid, []))
                    rows = [r for r in rows if r["entity_id"] in selected_ids]
                else:
                    # No selection: take all (server-side default; UI usually picks)
                    pass
                # Drop entities pointing to agent_ids OUTSIDE the closure (entity_type='agent')
                kept = []
                for r in rows:
                    if r.get("entity_type") == "agent" and r.get("entity_id") not in closure_set:
                        warnings.append(
                            f"skipped_external_edge: {aid} -> {r.get('entity_id')} (not in bundle)"
                        )
                        continue
                    kept.append(r)
                social_entities_for_agent.extend(kept)
            (agent_dir / "social_entities.json").write_text(
                json.dumps([_scrub_user_id(dict(r), user_id) for r in social_entities_for_agent],
                           indent=2, ensure_ascii=False, default=str),
                encoding="utf-8",
            )

            # RAG store rows
            rag_rows = []
            for iid in agent_instance_ids:
                rag_rows.extend(await db.get("instance_rag_store", {"instance_id": iid}))
            (agent_dir / "rag.json").write_text(
                json.dumps([_scrub_user_id(dict(r), user_id) for r in rag_rows],
                           indent=2, ensure_ascii=False, default=str),
                encoding="utf-8",
            )

            # agent_messages
            msg_rows = await db.get("agent_messages", {"agent_id": aid}) if selection.include_chat_history else []
            (agent_dir / "agent_messages.jsonl").write_text(
                "\n".join(json.dumps(_scrub_user_id(dict(r), user_id),
                                     ensure_ascii=False, default=str) for r in msg_rows),
                encoding="utf-8",
            )

            # workspace tar.gz
            ws_path = await _pack_workspace(aid, user_id, agent_dir,
                                            excludes=selection.workspace_excludes.get(aid, []))
            agents_summary.append({
                "agent_id": aid,
                "agent_name": agent_row["agent_name"],
                "narratives": len(n_rows),
                "instances": len(agent_instance_ids),
                "social_entities": len(social_entities_for_agent),
                "rag_rows": len(rag_rows),
                "workspace_size_bytes": ws_path.stat().st_size if ws_path else 0,
                "workspace_path": "workspace.tar.gz" if ws_path else None,
            })

        # ---- skills ----
        skills_dir = tmpdir / "skills"
        skills_dir.mkdir(parents=True, exist_ok=True)
        skills_summary = []
        zip_secrets_warnings: List[Dict[str, Any]] = []
        for skill_name, cfg in (selection.skill_methods or {}).items():
            method = cfg.get("install_method")
            entry = {
                "name": skill_name,
                "install_method": method,
                "contains_secrets": method == "full_copy",
            }
            if method == "url":
                entry["source_url"] = cfg.get("source_url")
                entry["source_type"] = cfg.get("source_type", "github")
                entry["branch"] = cfg.get("branch", "main")
            elif method == "zip":
                # archive_ref: relative path inside bundle
                src_zip = cfg.get("archive_path") or cfg.get("manual_zip_path")
                if not src_zip or not Path(src_zip).exists():
                    warnings.append(f"skill {skill_name}: zip not found, skipping")
                    continue
                # Scan for secrets (PRD §8.12.11)
                hits = scan_zip_for_sensitive(Path(src_zip))
                if hits:
                    zip_secrets_warnings.append({"skill": skill_name, "hits": hits})
                tgt_zip = skills_dir / f"{skill_name}.zip"
                await asyncio.to_thread(shutil.copy2, src_zip, tgt_zip)
                entry["archive_ref"] = f"skills/{skill_name}.zip"
                entry["sha256"] = await asyncio.to_thread(file_sha256, tgt_zip)
            elif method == "full_copy":
                # Pack the entire skill dir from agent's workspace
                # We pick the first agent that has this skill installed
                src_dir = await _find_skill_dir(closure_set, user_id, skill_name)
                if not src_dir:
                    warnings.append(f"skill {skill_name}: full_copy source not found")
                    continue
                tgt_zip = skills_dir / f"{skill_name}-full.zip"
                await asyncio.to_thread(_zip_dir, src_dir, tgt_zip)
                entry["archive_ref"] = f"skills/{skill_name}-full.zip"
                entry["sha256"] = await asyncio.to_thread(file_sha256, tgt_zip)
            elif method == "builtin":
                pass
            else:
                warnings.append(f"skill {skill_name}: unknown install_method {method}")
                continue
            skills_summary.append(entry)

        if zip_secrets_warnings:
            for w in zip_secrets_warnings:
                warnings.append(
                    f"sensitive_files_in_zip: {w['skill']} -> {','.join(w['hits'][:5])}"
                )

        # ---- README.md (Bundle Notes / team intro) ----
        if selection.team_intro_md.strip():
            (tmpdir / "README.md").write_text(selection.team_intro_md, encoding="utf-8")

        # ---- mcp_hints.json ----
        mcp_rows = []
        for aid in closure_set:
            rows = await db.get("mcp_urls", {"agent_id": aid, "user_id": user_id})
            for r in rows:
                mcp_rows.append({
                    "agent_id": aid,
                    "name": r["name"],
                    "url": r["url"],
                    "description": r.get("description"),
                })
        (tmpdir / "mcp_hints.json").write_text(
            json.dumps(mcp_rows, indent=2, ensure_ascii=False, default=str), encoding="utf-8"
        )

        # ---- manifest ----
        team_meta = None
        if selection.team_id:
            team_row = await db.get_one("teams", {"team_id": selection.team_id})
            if team_row:
                team_meta = {
                    "team_id": team_row["team_id"],
                    "name": team_row["name"],
                    "description": team_row.get("description"),
                    "color": team_row.get("color"),
                    "source": "bundle",
                    "intro_md": selection.team_intro_md or team_row.get("intro_md") or "",
                }

        manifest = {
            "bundle_format_version": BUNDLE_FORMAT_VERSION,
            "narranexus_version_exported": "1.3.4",
            "exported_at": datetime.now(timezone.utc).isoformat(),
            "owner_placeholder": "<original_owner>",
            "team": team_meta,
            "agents": list(closure_set),
            "agents_summary": agents_summary,
            "skills": skills_summary,
            "mcp_hints_count": len(mcp_rows),
            "stripped": stripped_lists,
            "warnings": warnings,
            "embedding": {
                "provider": selection.embedding_provider,
                "model": selection.embedding_model,
                "dim": selection.embedding_dim,
            },
        }

        # Compute integrity sha256 over all non-manifest files (sorted) — heavy I/O,
        # offloaded to a worker thread so we don't block the event loop while
        # hashing large workspaces.
        manifest["integrity_sha256"] = await asyncio.to_thread(
            _compute_integrity_sha256, tmpdir
        )

        (tmpdir / "manifest.json").write_text(
            json.dumps(manifest, indent=2, ensure_ascii=False, default=str),
            encoding="utf-8",
        )

        # ---- zip into .nxbundle ---- (also CPU-heavy, off-loop)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        await asyncio.to_thread(_zip_dir, tmpdir, output_path)

    return {
        "manifest": manifest,
        "warnings": warnings,
        "output_path": str(output_path),
    }


def _scrub_user_id(row: dict, user_id: str) -> dict:
    """Replace any user_id columns with the owner placeholder. Drop password_hash etc."""
    out = dict(row)
    for k in list(out.keys()):
        if k in ("password_hash", "secret", "api_key"):
            out.pop(k, None)
        elif k.endswith("user_id") or k == "user_id" or k == "created_by":
            if out[k] == user_id:
                out[k] = "<original_owner>"
    return out


async def _pack_workspace(
    agent_id: str,
    user_id: str,
    agent_dir: Path,
    excludes: List[str],
) -> Optional[Path]:
    """Tar.gz agent's workspace dir to agent_dir/workspace.tar.gz; respects sensitive-pattern filter.
    The tarfile compression itself is offloaded to a worker thread so the main
    asyncio event loop stays responsive for other users during big workspaces."""
    # SINGLE-WORKER ASSUMPTION: workspaces live on this process's local fs.
    # Multi-pod scale requires shared volume (compose already mounts it
    # under /opt/narranexus/workspaces). See
    # .mindflow/project/references/scaling_assumptions.md §3.
    candidates = [
        Path.home() / ".nexusagent" / "workspaces" / f"agent_{agent_id.replace('agent_', '')}_user_{user_id}",
        Path.home() / ".nexusagent" / "workspaces" / f"{agent_id}_user_{user_id}",
        Path.home() / ".nexusagent" / "workspaces" / f"{agent_id}_{user_id}",
    ]
    src = next((c for c in candidates if c.is_dir()), None)
    if not src:
        return None

    out = agent_dir / "workspace.tar.gz"
    excl_set = set(excludes or [])

    return await asyncio.to_thread(_pack_workspace_sync, src, out, excl_set)


def _pack_workspace_sync(src: Path, out: Path, excl_set: set) -> Path:
    def filter_func(tarinfo: tarfile.TarInfo) -> Optional[tarfile.TarInfo]:
        name = tarinfo.name
        if name.startswith("./"):
            name = name[2:]
        # never include symlinks
        if tarinfo.issym() or tarinfo.islnk():
            return None
        # user explicit excludes
        if name in excl_set:
            return None
        # sensitive default-skip
        if is_sensitive_path(name):
            return None
        # volume default-skip
        if is_volume_path(name):
            return None
        return tarinfo

    with tarfile.open(out, "w:gz") as tar:
        tar.add(src, arcname=".", filter=filter_func)
    return out


async def _find_skill_dir(agent_ids: Set[str], user_id: str, skill_name: str) -> Optional[Path]:
    for aid in agent_ids:
        candidates = [
            Path.home() / ".nexusagent" / "workspaces" / f"{aid}_user_{user_id}" / "skills" / skill_name,
            Path.home() / ".nexusagent" / "workspaces" / f"{aid}_{user_id}" / "skills" / skill_name,
        ]
        for c in candidates:
            if c.is_dir():
                return c
    return None


def _zip_dir(src: Path, dst: Path) -> None:
    """Synchronous zip writer. Callers in async context MUST wrap in
    asyncio.to_thread() — zip compression is CPU-bound and blocks the event loop."""
    with zipfile.ZipFile(dst, "w", zipfile.ZIP_DEFLATED) as zf:
        for root, _, files in os.walk(src):
            for fn in files:
                full = Path(root) / fn
                rel = full.relative_to(src)
                zf.write(full, arcname=str(rel))


def _compute_integrity_sha256(tmpdir: Path) -> str:
    """Walk tmpdir, sha256 each non-manifest file, fold into a single digest.
    Sync — caller wraps in asyncio.to_thread()."""
    all_paths = sorted(
        [p for p in tmpdir.rglob("*") if p.is_file() and p.name != "manifest.json"]
    )
    h = io.BytesIO()
    for p in all_paths:
        h.write(str(p.relative_to(tmpdir)).encode("utf-8"))
        h.write(b":")
        h.write(file_sha256(p).encode("utf-8"))
        h.write(b"\n")
    return bytes_sha256(h.getvalue())
