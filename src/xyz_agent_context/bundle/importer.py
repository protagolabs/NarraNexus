"""
@file_name: importer.py
@author: NetMind.AI
@date: 2026-05-08
@description: Bundle import pipeline — preflight + confirm

Pipeline (PRD §8.4):
1. Form check (size, zip, manifest)
2. Security (zip-bomb, traversal, sha256)
3. Compatibility (schema_version, embedding compat)
4. ID rewrite (5 layers: kind regex + structured field map + free-text regex)
5. Name suffix dedupe ("Trading Bot (1)")
6. user_id injection
7. Transactional write
8. Summary
"""

import asyncio
import io
import json
import os
import re
import shutil
import tarfile
import tempfile
import uuid
import zipfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional
from loguru import logger

from xyz_agent_context.utils.db_factory import get_db_client
from .id_field_map import STRUCTURED_ID_FIELDS, gen_new_id
from .id_schema import build_all_id_regex, ID_KINDS
from .security import (
    extract_zip_safely,
    file_sha256,
    bytes_sha256,
    MAX_BUNDLE_BYTES,
)


# Preflight session TTL — older sessions are pruned every preflight call.
PREFLIGHT_TTL_HOURS = 6


async def _cleanup_stale_preflights(db) -> None:
    """Delete preflight rows older than PREFLIGHT_TTL_HOURS + their work_dirs.
    Called inline from preflight() — cheap; expected row count is tiny."""
    cutoff = (datetime.now(timezone.utc) - timedelta(hours=PREFLIGHT_TTL_HOURS)).strftime(
        "%Y-%m-%d %H:%M:%S"
    )
    rows = await db.execute(
        "SELECT token, work_dir FROM bundle_preflight_sessions WHERE created_at < %s",
        params=(cutoff,),
        fetch=True,
    )
    for r in rows or []:
        wd = r.get("work_dir")
        if wd:
            try:
                shutil.rmtree(wd, ignore_errors=True)
            except Exception:
                pass
    if rows:
        await db.execute(
            "DELETE FROM bundle_preflight_sessions WHERE created_at < %s",
            params=(cutoff,),
            fetch=False,
        )


async def preflight(zip_path: Path, user_id: str) -> Dict[str, Any]:
    """Validate the bundle and report what would be created.
    Returns: {preflight_token, manifest, warnings, name_clashes, embedding_compat}"""
    if not zip_path.exists():
        raise ValueError("zip_path does not exist")
    size = zip_path.stat().st_size
    if size > MAX_BUNDLE_BYTES:
        raise ValueError(f"bundle too large: {size}B > {MAX_BUNDLE_BYTES}B")

    # Extract to a persistent dir (kept until /confirm completes or TTL expires).
    # Use a shared volume-friendly path under ~/.nexusagent so docker compose
    # can persist it across backend restarts (mount point set by ops).
    # SINGLE-WORKER ASSUMPTION: this path is on the local fs of whichever
    # backend process handled preflight. Multi-pod scale needs a shared
    # volume (RWX) or object-store rewrite — see
    # .mindflow/project/references/scaling_assumptions.md §1.
    sessions_root = Path.home() / ".nexusagent" / "bundle_preflight"
    sessions_root.mkdir(parents=True, exist_ok=True)
    work_dir = Path(tempfile.mkdtemp(prefix="nx-import-", dir=str(sessions_root)))
    try:
        # zip extraction is CPU-bound — offload to a worker thread.
        await asyncio.to_thread(extract_zip_safely, zip_path, work_dir)
    except Exception:
        shutil.rmtree(work_dir, ignore_errors=True)
        raise

    manifest_path = work_dir / "manifest.json"
    if not manifest_path.exists():
        shutil.rmtree(work_dir, ignore_errors=True)
        raise ValueError("manifest.json missing in bundle")

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

    # Legacy bundle compatibility: pre-3d7e089 exports put every
    # `skipped_external_edge` line into manifest.warnings (one per row).
    # That floods the preflight UI with hundreds of "Bundle warnings"
    # entries that are actually expected closure-drop events. Demote them
    # to manifest.info here so any bundle (old or new) renders the same
    # in the import wizard.
    raw_warnings = manifest.get("warnings") or []
    real_warnings: List[str] = []
    legacy_external_edge_count = 0
    for w in raw_warnings:
        if isinstance(w, str) and w.startswith("skipped_external_edge:"):
            legacy_external_edge_count += 1
        else:
            real_warnings.append(w)
    if legacy_external_edge_count > 0:
        manifest["warnings"] = real_warnings
        info = list(manifest.get("info") or [])
        info.append(
            f"skipped {legacy_external_edge_count} external entity reference(s) "
            "outside the bundle closure (expected — see PRD §8.3)"
        )
        manifest["info"] = info
        info_counters = dict(manifest.get("info_counters") or {})
        info_counters["skipped_external_edge"] = (
            info_counters.get("skipped_external_edge", 0) + legacy_external_edge_count
        )
        manifest["info_counters"] = info_counters

    # Compatibility: walk the bundle-format-version migration chain so older
    # bundles can be upgraded in-place to the current major before import.
    from xyz_agent_context.bundle._bundle_migrations import (
        apply_migrations,
        CURRENT_BUNDLE_MAJOR,
    )
    try:
        manifest = await apply_migrations(work_dir, manifest)
    except ValueError:
        shutil.rmtree(work_dir, ignore_errors=True)
        raise
    # Persist the (possibly migrated) manifest back to disk so confirm() reads
    # the upgraded version.
    manifest_path.write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False, default=str),
        encoding="utf-8",
    )

    # Name clashes
    db = await get_db_client()
    name_clashes: List[Dict[str, str]] = []
    for aid in manifest.get("agents", []):
        agent_path = work_dir / "agents" / aid / "agent.json"
        if not agent_path.exists():
            continue
        a = json.loads(agent_path.read_text(encoding="utf-8"))
        existing = await db.get(
            "agents", {"agent_name": a["agent_name"], "created_by": user_id}
        )
        if existing:
            name_clashes.append({
                "agent_id_in_bundle": aid,
                "agent_name": a["agent_name"],
                "existing_count": len(existing),
            })
    team_clash = None
    team = manifest.get("team")
    if team:
        existing_team = await db.get(
            "teams", {"name": team["name"], "owner_user_id": user_id}
        )
        if existing_team:
            team_clash = {"name": team["name"], "existing_count": len(existing_team)}

    # Embedding compatibility — manifest's value vs current instance's default.
    # Hard-block when the dim is incompatible (the embeddings_store column would
    # mismatch and break semantic search). When provider/model differ but dim
    # matches, surface a soft warning suggesting a rebuild.
    from xyz_agent_context.settings import settings as core_settings
    from xyz_agent_context.agent_framework.model_catalog import get_embedding_dimensions

    bundle_emb = manifest.get("embedding", {}) or {}
    bundle_dim = bundle_emb.get("dim")
    bundle_provider = bundle_emb.get("provider")
    bundle_model = bundle_emb.get("model")

    local_model = core_settings.openai_embedding_model
    local_dim = get_embedding_dimensions(local_model) if local_model else None
    local_provider = "openai"  # current implementation hard-codes openai

    embedding_compat: Dict[str, Any] = {
        "manifest": bundle_emb,
        "local": {"provider": local_provider, "model": local_model, "dim": local_dim},
        "compatible": True,
        "advice": "Embeddings from the bundle should work as-is.",
    }

    if bundle_dim is not None and local_dim is not None and bundle_dim != local_dim:
        # Hard block — different vector dimensions means the embeddings_store column
        # cannot be reused, RAG / chat embeddings would be silently broken.
        embedding_compat["compatible"] = False
        embedding_compat["advice"] = (
            f"BLOCKED: bundle uses embeddings of dimension {bundle_dim} but this "
            f"instance is configured for dimension {local_dim} (model={local_model}). "
            "Importing would corrupt the RAG store. Either reconfigure this instance "
            "to use a model with matching dim, or ask the bundle author to re-export "
            "with a compatible model."
        )
        # Refuse to create a session — this is an unrecoverable mismatch.
        shutil.rmtree(work_dir, ignore_errors=True)
        raise ValueError(embedding_compat["advice"])

    if bundle_provider and bundle_provider != local_provider:
        embedding_compat["advice"] = (
            f"WARNING: bundle was exported with embedding provider '{bundle_provider}'; "
            f"this instance uses '{local_provider}'. Existing embeddings will be kept "
            "but you may want to rebuild via Settings → Embedding Index."
        )
    elif bundle_model and bundle_model != local_model:
        embedding_compat["advice"] = (
            f"WARNING: bundle was exported with embedding model '{bundle_model}'; "
            f"this instance uses '{local_model}'. Vectors are dimensionally compatible "
            f"({local_dim}), but semantic space differs slightly. Optional rebuild via "
            "Settings → Embedding Index for best results."
        )

    token = uuid.uuid4().hex
    summary = {
        "preflight_token": token,
        "manifest": manifest,
        "name_clashes": name_clashes,
        "team_clash": team_clash,
        "embedding_compat": embedding_compat,
        "warnings": manifest.get("warnings", []),
    }
    # Persist to DB so confirm() works across worker boundaries / restarts.
    await _cleanup_stale_preflights(db)
    await db.insert(
        "bundle_preflight_sessions",
        {
            "token": token,
            "user_id": user_id,
            "work_dir": str(work_dir),
            "manifest_json": json.dumps(manifest, ensure_ascii=False, default=str),
        },
    )
    return summary


async def confirm(preflight_token: str, user_id: str) -> Dict[str, Any]:
    """Execute the actual import using a previously preflighted bundle."""
    db = await get_db_client()
    row = await db.get_one("bundle_preflight_sessions", {"token": preflight_token})
    if not row:
        raise ValueError("preflight_token not found or expired")
    if row["user_id"] != user_id:
        raise ValueError("preflight_token user mismatch")

    work_dir = Path(row["work_dir"])
    manifest = json.loads(row["manifest_json"])
    if not work_dir.exists():
        # Backend was restarted / volume not persisted; the extracted files are gone.
        await db.delete("bundle_preflight_sessions", {"token": preflight_token})
        raise ValueError("preflight working dir missing — please re-upload the bundle")

    # ---- ID Rewrite ----
    id_map: Dict[str, str] = {}

    # Collect ALL old IDs from structured fields
    for aid in manifest.get("agents", []):
        id_map[aid] = gen_new_id("agent")

    if manifest.get("team"):
        old_team_id = manifest["team"].get("team_id")
        if old_team_id:
            id_map[old_team_id] = gen_new_id("team")

    # Walk the per-agent dirs and pre-collect IDs
    agents_dir = work_dir / "agents"
    if agents_dir.exists():
        for adir in agents_dir.iterdir():
            if not adir.is_dir():
                continue
            # narratives
            ndir = adir / "narratives"
            if ndir.exists():
                for sub in ndir.iterdir():
                    if sub.is_dir():
                        if sub.name not in id_map:
                            id_map[sub.name] = gen_new_id("narrative")
                        # events.jsonl IDs
                        e_path = sub / "events.jsonl"
                        if e_path.exists():
                            with open(e_path, "r", encoding="utf-8") as f:
                                for line in f:
                                    line = line.strip()
                                    if not line:
                                        continue
                                    try:
                                        rec = json.loads(line)
                                    except json.JSONDecodeError:
                                        continue
                                    eid = rec.get("event_id")
                                    if eid and eid not in id_map:
                                        id_map[eid] = gen_new_id("event")
            # instances
            inst_dir = adir / "instances"
            if inst_dir.exists():
                for kdir in inst_dir.iterdir():
                    if kdir.is_dir():
                        for ifile in kdir.iterdir():
                            if ifile.suffix == ".json":
                                rec = json.loads(ifile.read_text(encoding="utf-8"))
                                iid = rec.get("instance_id")
                                if iid and iid not in id_map:
                                    id_map[iid] = gen_new_id("instance")
            # agent_messages
            am_path = adir / "agent_messages.jsonl"
            if am_path.exists():
                with open(am_path, "r", encoding="utf-8") as f:
                    for line in f:
                        line = line.strip()
                        if not line:
                            continue
                        try:
                            rec = json.loads(line)
                        except json.JSONDecodeError:
                            continue
                        mid = rec.get("message_id")
                        if mid and mid not in id_map:
                            id_map[mid] = gen_new_id("message")

    free_text_regex = build_all_id_regex()

    def rewrite_id(s: str) -> str:
        return id_map.get(s, s)

    def rewrite_text(text: str) -> str:
        if not isinstance(text, str):
            return text
        return free_text_regex.sub(lambda m: id_map.get(m.group(), m.group()), text)

    def rewrite_value(v: Any) -> Any:
        if isinstance(v, str):
            return rewrite_text(v)
        if isinstance(v, list):
            return [rewrite_value(x) for x in v]
        if isinstance(v, dict):
            return {k: rewrite_value(x) for k, x in v.items()}
        return v

    # Helper: write a row using the structured map and free-text rewrite
    def rewrite_row(table: str, row: dict) -> dict:
        out = dict(row)
        # Strip legacy auto-increment 'id' so we get a fresh PK
        out.pop("id", None)
        for col in list(out.keys()):
            val = out[col]
            kind = STRUCTURED_ID_FIELDS.get(table, {}).get(col)
            if kind and isinstance(val, str):
                out[col] = id_map.get(val, val)
            elif isinstance(val, str):
                out[col] = rewrite_text(val)
            elif isinstance(val, (list, dict)):
                out[col] = rewrite_value(val)
        return out

    # ---- Name suffix dedupe ----
    db = await get_db_client()

    async def dedupe_name(table: str, name_col: str, owner_filter: dict, candidate: str) -> str:
        existing = await db.get(table, {**owner_filter, name_col: candidate})
        if not existing:
            return candidate
        n = 1
        while True:
            new_name = f"{candidate} ({n})"
            existing = await db.get(table, {**owner_filter, name_col: new_name})
            if not existing:
                return new_name
            n += 1

    # ---- WRITE phase (best-effort transactional via try/except + rollback notes) ----
    written_summary = {
        "agents_created": 0,
        "agents_renamed": 0,
        "team_created": False,
        "narratives_created": 0,
        "events_created": 0,
        "instances_created": 0,
        "messages_created": 0,
        "social_entities_created": 0,
        "rag_rows_created": 0,
        "skills_imported": 0,
        "mcp_hints": 0,
        "warnings": [],
    }

    # -- Team --
    new_team_id = None
    if manifest.get("team"):
        team = manifest["team"]
        old_tid = team.get("team_id")
        new_tid = id_map.get(old_tid) if old_tid else gen_new_id("team")
        if not old_tid:
            id_map["__team_synthetic__"] = new_tid

        team_name = await dedupe_name(
            "teams", "name", {"owner_user_id": user_id}, team["name"]
        )
        intro = team.get("intro_md", "")
        if not intro:
            readme_path = work_dir / "README.md"
            if readme_path.exists():
                intro = readme_path.read_text(encoding="utf-8")
        await db.insert("teams", {
            "team_id": new_tid,
            "owner_user_id": user_id,
            "name": team_name,
            "description": team.get("description"),
            "color": team.get("color"),
            "source": f"bundle",
            "intro_md": intro,
        })
        new_team_id = new_tid
        written_summary["team_created"] = True
        written_summary["team_id"] = new_tid
        written_summary["team_name"] = team_name

    # -- Per-agent write --
    for old_aid in manifest.get("agents", []):
        adir = work_dir / "agents" / old_aid
        if not adir.is_dir():
            continue
        agent_path = adir / "agent.json"
        if not agent_path.exists():
            continue
        agent_record = json.loads(agent_path.read_text(encoding="utf-8"))
        new_aid = id_map[old_aid]

        original_name = agent_record["agent_name"]
        deduped_name = await dedupe_name(
            "agents", "agent_name", {"created_by": user_id}, original_name
        )
        if deduped_name != original_name:
            written_summary["agents_renamed"] += 1

        # Insert agents row (new agent_id, current user_id)
        new_agent_row = rewrite_row("agents", agent_record)
        new_agent_row["agent_id"] = new_aid
        new_agent_row["agent_name"] = deduped_name
        new_agent_row["created_by"] = user_id
        new_agent_row.pop("agent_create_time", None)
        new_agent_row.pop("agent_update_time", None)
        await db.insert("agents", new_agent_row)
        written_summary["agents_created"] += 1

        # team_members
        if new_team_id:
            await db.insert("team_members", {"team_id": new_team_id, "agent_id": new_aid})

        # Narratives + events
        ndir = adir / "narratives"
        if ndir.exists():
            for nsub in ndir.iterdir():
                if not nsub.is_dir():
                    continue
                nfile = nsub / "narrative.json"
                if not nfile.exists():
                    continue
                nrow = json.loads(nfile.read_text(encoding="utf-8"))
                new_nrow = rewrite_row("narratives", nrow)
                new_nrow.pop("created_at", None)
                new_nrow.pop("updated_at", None)
                await db.insert("narratives", new_nrow)
                written_summary["narratives_created"] += 1

                e_path = nsub / "events.jsonl"
                if e_path.exists():
                    with open(e_path, "r", encoding="utf-8") as f:
                        for line in f:
                            line = line.strip()
                            if not line:
                                continue
                            try:
                                erec = json.loads(line)
                            except json.JSONDecodeError:
                                continue
                            new_erow = rewrite_row("events", erec)
                            new_erow.pop("created_at", None)
                            new_erow.pop("updated_at", None)
                            new_erow["user_id"] = user_id
                            await db.insert("events", new_erow)
                            written_summary["events_created"] += 1

        # instances
        inst_dir = adir / "instances"
        if inst_dir.exists():
            for kdir in inst_dir.iterdir():
                if not kdir.is_dir():
                    continue
                for ifile in kdir.iterdir():
                    if ifile.suffix != ".json":
                        continue
                    irec = json.loads(ifile.read_text(encoding="utf-8"))
                    new_irow = rewrite_row("module_instances", irec)
                    new_irow["user_id"] = user_id
                    new_irow.pop("created_at", None)
                    new_irow.pop("updated_at", None)
                    await db.insert("module_instances", new_irow)
                    written_summary["instances_created"] += 1

        # social entities
        se_path = adir / "social_entities.json"
        if se_path.exists():
            for srec in json.loads(se_path.read_text(encoding="utf-8")):
                new_sr = rewrite_row("instance_social_entities", srec)
                new_sr.pop("created_at", None)
                new_sr.pop("updated_at", None)
                # entity_id might be an agent_id in our closure
                if srec.get("entity_type") == "agent":
                    eid = new_sr.get("entity_id")
                    if eid in id_map:
                        new_sr["entity_id"] = id_map[eid]
                await db.insert("instance_social_entities", new_sr)
                written_summary["social_entities_created"] += 1

        # rag store
        rag_path = adir / "rag.json"
        if rag_path.exists():
            for rrec in json.loads(rag_path.read_text(encoding="utf-8")):
                new_rr = rewrite_row("instance_rag_store", rrec)
                new_rr.pop("created_at", None)
                new_rr.pop("updated_at", None)
                await db.insert("instance_rag_store", new_rr)
                written_summary["rag_rows_created"] += 1

        # agent_messages
        am_path = adir / "agent_messages.jsonl"
        if am_path.exists():
            with open(am_path, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        mrec = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    new_mr = rewrite_row("agent_messages", mrec)
                    new_mr.pop("created_at", None)
                    await db.insert("agent_messages", new_mr)
                    written_summary["messages_created"] += 1

        # workspace tar — offload extraction to thread.
        # Use canonical {agent_id}_{user_id} path (matches attachment_storage
        # and step_3_agent_loop). New imports always use the canonical form.
        ws_tar = adir / "workspace.tar.gz"
        if ws_tar.exists():
            from xyz_agent_context.settings import settings as core_settings
            target = Path(core_settings.base_working_path) / f"{new_aid}_{user_id}"
            target.mkdir(parents=True, exist_ok=True)
            await asyncio.to_thread(_extract_tar_safely, ws_tar, target)
            # Layer 4 (PRD §8.11): rewrite IDs in extracted text files too.
            await asyncio.to_thread(_rewrite_workspace_text_files, target, id_map)

    # -- Skills (auto-install per-(agent, skill)) --
    # Each manifest.skills entry has agent_id (the OLD agent_id from the
    # source instance). Map it to the new agent_id and install the skill
    # only on that one — preserves per-agent state from the source.
    skill_archives_dir = Path.home() / ".nexusagent" / "skill_archives" / user_id
    skill_archives_dir.mkdir(parents=True, exist_ok=True)
    skill_install_failures: List[Dict[str, str]] = []

    from xyz_agent_context.module.skill_module.skill_module import SkillModule
    from xyz_agent_context.bundle.skill_backup import (
        backup_after_api_install,
        register_archive,
    )

    for s in manifest.get("skills", []):
        method = s.get("install_method")
        skill_name = s.get("name")
        old_aid = s.get("agent_id")
        # Backward compat: pre-per-agent bundles had no agent_id; install on all.
        target_aids = []
        if old_aid:
            new_aid = id_map.get(old_aid)
            if new_aid:
                target_aids = [new_aid]
            else:
                skill_install_failures.append(
                    {"skill": skill_name, "reason": f"agent {old_aid} not in id_map"}
                )
                continue
        else:
            # Legacy bundle: apply this skill to every imported agent.
            target_aids = [id_map[old] for old in manifest.get("agents", []) if old in id_map]

        if not skill_name:
            continue
        try:
            if method == "url":
                src_url = s.get("source_url")
                branch = s.get("branch") or "main"
                if not src_url:
                    skill_install_failures.append(
                        {"skill": skill_name, "reason": "url install method but no source_url"}
                    )
                    continue
                for new_aid in target_aids:
                    sm = SkillModule(agent_id=new_aid, user_id=user_id)
                    await asyncio.to_thread(sm.install_from_github, src_url, branch)
                await backup_after_api_install(
                    user_id=user_id,
                    skill_name=skill_name,
                    source_type="github",
                    source_url=src_url,
                    branch=branch,
                )
                written_summary["skills_imported"] += 1

            elif method == "zip":
                archive_ref = s.get("archive_ref")
                zip_path = work_dir / archive_ref if archive_ref else None
                if not zip_path or not zip_path.exists():
                    skill_install_failures.append(
                        {"skill": skill_name, "reason": "zip archive missing in bundle"}
                    )
                    continue
                tgt = skill_archives_dir / f"{skill_name}.zip"
                if not tgt.exists():
                    await asyncio.to_thread(shutil.copy2, zip_path, tgt)
                for new_aid in target_aids:
                    sm = SkillModule(agent_id=new_aid, user_id=user_id)
                    await asyncio.to_thread(sm.install_skill, zip_path)
                await backup_after_api_install(
                    user_id=user_id,
                    skill_name=skill_name,
                    source_type="zip",
                    source_url=None,
                    original_zip_path=tgt,
                )
                written_summary["skills_imported"] += 1

            elif method == "full_copy":
                # full_copy is per-agent: each agent's archive_ref points at
                # skills/<agent_id>/<skill>-full.zip with that agent's own
                # .skill_meta.json + wallets.
                archive_ref = s.get("archive_ref")
                zip_path = work_dir / archive_ref if archive_ref else None
                if not zip_path or not zip_path.exists():
                    skill_install_failures.append(
                        {"skill": skill_name, "reason": "full_copy archive missing in bundle"}
                    )
                    continue
                # Stash a copy in skill_archives for re-export (uses last-seen).
                tgt = skill_archives_dir / f"{skill_name}_full.zip"
                await asyncio.to_thread(shutil.copy2, zip_path, tgt)
                for new_aid in target_aids:
                    sm = SkillModule(agent_id=new_aid, user_id=user_id)
                    await asyncio.to_thread(sm.install_skill, zip_path)
                await register_archive(
                    user_id=user_id,
                    skill_name=skill_name,
                    source_type="zip",
                    source_url=None,
                    archive_path=str(tgt),
                    sha256=s.get("sha256", "imported"),
                )
                written_summary["skills_imported"] += 1

            elif method == "builtin":
                written_summary["skills_imported"] += 1

            else:
                skill_install_failures.append(
                    {"skill": skill_name, "reason": f"unknown install_method '{method}'"}
                )
        except Exception as e:
            logger.exception(f"skill install failed for '{skill_name}' on {old_aid}: {e}")
            skill_install_failures.append({"skill": f"{skill_name}@{old_aid}", "reason": str(e)})

    if skill_install_failures:
        written_summary["warnings"].extend([
            f"skill install failed: {f['skill']} — {f['reason']}"
            for f in skill_install_failures
        ])
        written_summary["skill_install_failures"] = skill_install_failures

    # -- mcp_hints --
    mcp_hints_path = work_dir / "mcp_hints.json"
    if mcp_hints_path.exists():
        hints = json.loads(mcp_hints_path.read_text(encoding="utf-8"))
        written_summary["mcp_hints"] = len(hints)
        written_summary["mcp_hints_data"] = hints  # frontend prompts user

    # cleanup
    try:
        shutil.rmtree(work_dir, ignore_errors=True)
    finally:
        await db.delete("bundle_preflight_sessions", {"token": preflight_token})

    return written_summary


def _extract_tar_safely(tar_path: Path, target: Path) -> None:
    """Synchronous tar.gz extractor with traversal/symlink guards.
    Caller wraps in asyncio.to_thread() — extraction is CPU+IO bound."""
    with tarfile.open(tar_path, "r:gz") as tar:
        safe_members = []
        for member in tar.getmembers():
            if member.issym() or member.islnk():
                continue
            if member.name.startswith("/") or ".." in member.name.split("/"):
                continue
            safe_members.append(member)
        tar.extractall(target, members=safe_members)


# Extensions whose contents we Layer-4 scan for ID rewrite.
_TEXT_EXTENSIONS = {".md", ".txt", ".json", ".jsonl", ".yaml", ".yml", ".csv", ".log"}
_MAX_TEXT_REWRITE_SIZE = 5 * 1024 * 1024  # 5 MB per file — anything bigger we skip


def _rewrite_workspace_text_files(root: Path, id_map: Dict[str, str]) -> None:
    """Walk extracted workspace, regex-rewrite known ID kinds in text files.
    Layer 4 of PRD §8.11. Sync — caller wraps in asyncio.to_thread()."""
    if not id_map:
        return
    rgx = build_all_id_regex()

    def replace(m):
        old = m.group()
        return id_map.get(old, old)

    for path in root.rglob("*"):
        if not path.is_file():
            continue
        if path.suffix.lower() not in _TEXT_EXTENSIONS:
            continue
        try:
            if path.stat().st_size > _MAX_TEXT_REWRITE_SIZE:
                continue
            with open(path, "r", encoding="utf-8") as f:
                content = f.read()
        except (OSError, UnicodeDecodeError):
            continue
        new_content = rgx.sub(replace, content)
        if new_content != content:
            try:
                with open(path, "w", encoding="utf-8") as f:
                    f.write(new_content)
            except OSError:
                pass
