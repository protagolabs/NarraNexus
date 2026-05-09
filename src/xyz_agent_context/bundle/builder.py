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


class SensitiveZipDetected(Exception):
    """Raised when a zip-source skill's archive contains sensitive paths and
    the caller has not confirmed via `accept_sensitive_zips=True`."""

    def __init__(self, hits: List[Dict[str, Any]]):
        self.hits = hits  # [{skill, hits: [path, ...]}, ...]
        super().__init__(
            f"{len(hits)} skill(s) contain sensitive files in their zip archive. "
            "User must explicitly accept before export proceeds."
        )


class ExportSelection:
    """Agent-scoped selection for what to include in the bundle."""

    def __init__(
        self,
        agent_ids: List[str],
        team_id: Optional[str] = None,
        team_intro_md: Optional[str] = None,
        skill_methods: Optional[List[Dict[str, Any]]] = None,
        social_entity_selection: Optional[Dict[str, List[str]]] = None,
        workspace_excludes: Optional[Dict[str, List[str]]] = None,
        include_chat_history: bool = True,
        embedding_provider: Optional[str] = None,
        embedding_model: Optional[str] = None,
        embedding_dim: Optional[int] = None,
        accept_sensitive_zips: bool = False,
        narrative_selection: Optional[Dict[str, List[str]]] = None,
        event_selection: Optional[Dict[str, List[str]]] = None,
    ):
        self.agent_ids = agent_ids
        self.team_id = team_id
        self.team_intro_md = team_intro_md or ""
        # skill_methods: list of per-(agent, skill) export specs.
        # Each entry: {agent_id, skill_name, install_method, source_url?,
        #              source_type?, branch?, archive_path?, manual_zip_path?}
        # The same skill name on N agents = N entries in this list, and the
        # builder serializes one bundle entry per row.
        self.skill_methods = skill_methods or []
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
        # B6: explicit user opt-in to ship zip-archived skills containing
        # sensitive files (e.g. .env / wallet.json). Without this, builder
        # raises SensitiveZipDetected and the route returns 409 with the hits
        # so the frontend can show a confirmation modal.
        self.accept_sensitive_zips = accept_sensitive_zips
        # B2: per-agent narrative_id allowlist (None = include all).
        self.narrative_selection = narrative_selection
        # B2: per-narrative event_id allowlist (None = include all).
        self.event_selection = event_selection


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
    # `info` is for expected, non-actionable events (e.g. closure dropped an
    # external agent reference). The frontend shows `len(warnings)`, not info,
    # so a clean export with thousands of dropped external edges no longer
    # looks like "1234 warnings 你完了".
    info: List[str] = []
    info_counters: Dict[str, int] = {"skipped_external_edge": 0}
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

            # narratives + events — B2: respect per-agent narrative_selection
            # and per-narrative event_selection if provided. None = all.
            narratives_dir = agent_dir / "narratives"
            narratives_dir.mkdir(parents=True, exist_ok=True)
            n_rows = await db.get("narratives", {"agent_id": aid})
            allowed_nars = (
                selection.narrative_selection.get(aid)
                if selection.narrative_selection
                else None
            )
            for n in n_rows:
                if allowed_nars is not None and n["narrative_id"] not in allowed_nars:
                    continue
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
                    allowed_events = (
                        set(selection.event_selection.get(n["narrative_id"], []))
                        if selection.event_selection
                        else None
                    )
                    e_path = ndir / "events.jsonl"
                    with open(e_path, "w", encoding="utf-8") as f:
                        for e in e_rows:
                            if allowed_events is not None and e["event_id"] not in allowed_events:
                                continue
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
                        # Expected behavior under strict closure (PRD §8.3 step 4).
                        # Don't flood manifest.warnings — keep a counter and
                        # emit a single rolled-up entry at the end.
                        info_counters["skipped_external_edge"] += 1
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

            # instance_jobs (per-agent jobs created by JobModule)
            job_rows = await db.get("instance_jobs", {"agent_id": aid})
            (agent_dir / "jobs.json").write_text(
                json.dumps([_scrub_user_id(dict(r), user_id) for r in job_rows],
                           indent=2, ensure_ascii=False, default=str),
                encoding="utf-8",
            )

            # instance_narrative_links (per-instance narrative bindings)
            nl_rows = []
            for iid in agent_instance_ids:
                nl_rows.extend(await db.get("instance_narrative_links", {"instance_id": iid}))
            (agent_dir / "instance_narrative_links.json").write_text(
                json.dumps([_scrub_user_id(dict(r), user_id) for r in nl_rows],
                           indent=2, ensure_ascii=False, default=str),
                encoding="utf-8",
            )

            # Per-instance memory family
            for memory_table in (
                "instance_module_report_memory",
                "instance_json_format_memory",
                "instance_json_format_memory_chat",
                "module_report_memory",
            ):
                mem_rows = []
                for iid in agent_instance_ids:
                    mem_rows.extend(await db.get(memory_table, {"instance_id": iid}))
                (agent_dir / f"{memory_table}.json").write_text(
                    json.dumps([_scrub_user_id(dict(r), user_id) for r in mem_rows],
                               indent=2, ensure_ascii=False, default=str),
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

        # ---- skills (per-(agent, skill) export) ----
        skills_dir = tmpdir / "skills"
        skills_dir.mkdir(parents=True, exist_ok=True)
        skills_summary = []
        zip_secrets_warnings: List[Dict[str, Any]] = []
        # De-dup zip-archive copies across multiple agents picking the same
        # zip-method skill: they'd reference the exact same source zip, so
        # one archive_ref is enough; per-agent bundle entries still each
        # point to it. Full-copy is always per-agent (different .skill_meta).
        copied_zip_ref: Dict[str, str] = {}  # skill_name → archive_ref already copied

        for cfg in (selection.skill_methods or []):
            agent_id = cfg.get("agent_id")
            skill_name = cfg.get("skill_name")
            method = cfg.get("install_method")
            if not agent_id or agent_id not in closure_set:
                warnings.append(
                    f"skill {skill_name}: agent {agent_id} not in closure, skipping"
                )
                continue
            entry: Dict[str, Any] = {
                "agent_id": agent_id,
                "name": skill_name,
                "install_method": method,
                "contains_secrets": method == "full_copy",
            }
            if method == "url":
                entry["source_url"] = cfg.get("source_url")
                entry["source_type"] = cfg.get("source_type", "github")
                entry["branch"] = cfg.get("branch", "main")
            elif method == "zip":
                # If this zip skill_name was already copied for another agent,
                # reuse the same archive_ref instead of duplicating bytes.
                if skill_name in copied_zip_ref:
                    entry["archive_ref"] = copied_zip_ref[skill_name]
                    entry["sha256"] = "shared"  # see manifest header for true hash
                else:
                    src_zip = cfg.get("archive_path") or cfg.get("manual_zip_path")
                    if not src_zip or not Path(src_zip).exists():
                        warnings.append(f"skill {skill_name} on {agent_id}: zip not found, skipping")
                        continue
                    hits = scan_zip_for_sensitive(Path(src_zip))
                    if hits:
                        zip_secrets_warnings.append({"skill": skill_name, "hits": hits})
                    tgt_zip = skills_dir / f"{skill_name}.zip"
                    await asyncio.to_thread(shutil.copy2, src_zip, tgt_zip)
                    archive_ref = f"skills/{skill_name}.zip"
                    copied_zip_ref[skill_name] = archive_ref
                    entry["archive_ref"] = archive_ref
                    entry["sha256"] = await asyncio.to_thread(file_sha256, tgt_zip)
            elif method == "full_copy":
                # Per-agent: pack THIS specific agent's skill dir
                src_dir = await _find_skill_dir({agent_id}, user_id, skill_name)
                if not src_dir:
                    warnings.append(f"skill {skill_name} on {agent_id}: full_copy source not found")
                    continue
                # Use per-agent path inside bundle to keep state separated.
                per_agent_dir = skills_dir / agent_id
                per_agent_dir.mkdir(parents=True, exist_ok=True)
                tgt_zip = per_agent_dir / f"{skill_name}-full.zip"
                await asyncio.to_thread(_zip_dir, src_dir, tgt_zip)
                entry["archive_ref"] = f"skills/{agent_id}/{skill_name}-full.zip"
                entry["sha256"] = await asyncio.to_thread(file_sha256, tgt_zip)
            elif method == "builtin":
                pass
            else:
                warnings.append(f"skill {skill_name} on {agent_id}: unknown install_method {method}")
                continue
            skills_summary.append(entry)

        if zip_secrets_warnings:
            if not selection.accept_sensitive_zips:
                # Force the caller (frontend) to surface a confirmation prompt
                # before we ship a bundle that contains user secrets.
                raise SensitiveZipDetected(zip_secrets_warnings)
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

        # ---- bus.json (cross-agent message bus state, closure-scoped) ----
        # Channels owned by closure agents; channel members in closure;
        # bus messages whose channel is in closure; bus_agent_registry rows
        # for closure agents.
        bus_channels = []
        bus_channel_members = []
        bus_messages = []
        bus_agent_registry = []
        # Channels: keep ones owned by any closure agent (owner_user_id == export user; we
        # ship all channels matching the owner_user_id and at least one closure member).
        owned_chs = await db.get("bus_channels", {"owner_user_id": user_id})
        kept_channel_ids: Set[str] = set()
        for ch in owned_chs:
            cid = ch["channel_id"]
            members = await db.get("bus_channel_members", {"channel_id": cid})
            closure_members = [m for m in members if m["agent_id"] in closure_set]
            if not closure_members:
                continue
            kept_channel_ids.add(cid)
            bus_channels.append(_scrub_user_id(dict(ch), user_id))
            bus_channel_members.extend(_scrub_user_id(dict(m), user_id) for m in closure_members)
        # Bus messages for kept channels
        for cid in kept_channel_ids:
            msgs = await db.get("bus_messages", {"channel_id": cid}, order_by="created_at ASC")
            bus_messages.extend(_scrub_user_id(dict(m), user_id) for m in msgs)
        # bus_agent_registry per closure agent
        for aid in closure_set:
            row = await db.get_one("bus_agent_registry", {"agent_id": aid})
            if row:
                bus_agent_registry.append(_scrub_user_id(dict(row), user_id))
        (tmpdir / "bus.json").write_text(
            json.dumps({
                "channels": bus_channels,
                "members": bus_channel_members,
                "messages": bus_messages,
                "registry": bus_agent_registry,
            }, indent=2, ensure_ascii=False, default=str),
            encoding="utf-8",
        )

        # ---- inbox.json (per-user inbox entries linked to events in closure) ----
        # We can't migrate the whole inbox (it's per-USER not per-agent), but
        # we DO want to migrate inbox entries triggered by the closure's events.
        # On import we'll filter to event_id ∈ id_map.
        inbox_rows = await db.get("inbox_table", {"user_id": user_id})
        inbox_kept = []
        # Find which event_ids are in our closure
        closure_event_ids: Set[str] = set()
        for aid in closure_set:
            for ev in await db.get("events", {"agent_id": aid}):
                closure_event_ids.add(ev["event_id"])
        for ib in inbox_rows:
            if ib.get("event_id") and ib["event_id"] in closure_event_ids:
                inbox_kept.append(_scrub_user_id(dict(ib), user_id))
        (tmpdir / "inbox.json").write_text(
            json.dumps(inbox_kept, indent=2, ensure_ascii=False, default=str),
            encoding="utf-8",
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

        # Roll up info_counters into one human-readable info line per kind
        if info_counters["skipped_external_edge"]:
            info.append(
                f"skipped {info_counters['skipped_external_edge']} external entity reference(s) "
                "outside the bundle closure (expected — see PRD §8.3)"
            )

        manifest = {
            "bundle_format_version": BUNDLE_FORMAT_VERSION,
            "narranexus_version_exported": "1.3.4",
            "exported_at": datetime.now(timezone.utc).isoformat(),
            "owner_placeholder": "<original_owner>",
            # We DON'T store the original user_id by name — privacy. The
            # placeholder above is the canonical token in all bundle text /
            # JSON; importer swaps it for the recipient's user_id everywhere.
            "team": team_meta,
            "agents": list(closure_set),
            "agents_summary": agents_summary,
            "skills": skills_summary,
            "mcp_hints_count": len(mcp_rows),
            "stripped": stripped_lists,
            "warnings": warnings,
            "info": info,
            "info_counters": info_counters,
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
    """Replace any user_id columns AND any free-text occurrences of user_id
    with the owner placeholder. Drop password_hash etc.

    Free-text replacement matters for fields like awareness markdown,
    event final_output, narrative dynamic_summary etc. that may contain the
    owner's username verbatim. On import the placeholder gets swapped for
    the recipient's user_id (Layer 4).
    """
    out = dict(row)
    placeholder = "<original_owner>"
    for k in list(out.keys()):
        v = out[k]
        if k in ("password_hash", "secret", "api_key"):
            out.pop(k, None)
            continue
        if k.endswith("user_id") or k == "user_id" or k == "created_by":
            if v == user_id:
                out[k] = placeholder
            continue
        # Free-text: replace literal user_id substring with placeholder.
        if isinstance(v, str) and user_id and user_id in v:
            out[k] = v.replace(user_id, placeholder)
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
    #
    # Canonical workspace path (settings.base_working_path / {agent_id}_{user_id})
    # comes from attachment_storage.get_workspace_path() and step_3_agent_loop.py.
    # Legacy `_user_<user_id>` infix kept as fallback for old install state.
    from xyz_agent_context.settings import settings as core_settings
    base = Path(core_settings.base_working_path)
    candidates = [
        base / f"{agent_id}_{user_id}",            # canonical
        base / f"{agent_id}_user_{user_id}",       # legacy
    ]
    src = next((c for c in candidates if c.is_dir()), None)
    if not src:
        return None

    out = agent_dir / "workspace.tar.gz"
    excl_set = set(excludes or [])

    return await asyncio.to_thread(_pack_workspace_sync, src, out, excl_set, user_id)


def _pack_workspace_sync(src: Path, out: Path, excl_set: set, user_id: str = "") -> Path:
    text_extensions = {".md", ".txt", ".json", ".jsonl", ".yaml", ".yml", ".csv", ".log"}
    placeholder = "<original_owner>"

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
        # If we need to scrub user_id from text files, walk manually so we can
        # rewrite their bytes before adding to tar. Otherwise, fast path.
        if user_id:
            for root, dirs, files in os.walk(src):
                for fn in files:
                    full = Path(root) / fn
                    rel_to_src = full.relative_to(src)
                    rel_str = str(rel_to_src).replace("\\", "/")
                    if rel_str in excl_set:
                        continue
                    if is_sensitive_path(rel_str) or is_volume_path(rel_str):
                        continue
                    if full.is_symlink():
                        continue
                    arcname = f"./{rel_str}"
                    if full.suffix.lower() in text_extensions:
                        try:
                            content = full.read_text(encoding="utf-8")
                        except (OSError, UnicodeDecodeError):
                            tar.add(full, arcname=arcname, filter=filter_func)
                            continue
                        if user_id in content:
                            content = content.replace(user_id, placeholder)
                            data = content.encode("utf-8")
                            ti = tarfile.TarInfo(name=arcname)
                            ti.size = len(data)
                            ti.mtime = full.stat().st_mtime
                            tar.addfile(ti, fileobj=io.BytesIO(data))
                            continue
                    tar.add(full, arcname=arcname, filter=filter_func)
        else:
            tar.add(src, arcname=".", filter=filter_func)
    return out


async def _find_skill_dir(agent_ids: Set[str], user_id: str, skill_name: str) -> Optional[Path]:
    from xyz_agent_context.settings import settings as core_settings
    base = Path(core_settings.base_working_path)
    for aid in agent_ids:
        candidates = [
            base / f"{aid}_{user_id}" / "skills" / skill_name,            # canonical
            base / f"{aid}_user_{user_id}" / "skills" / skill_name,       # legacy
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
