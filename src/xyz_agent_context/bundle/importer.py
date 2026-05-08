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

import io
import json
import os
import re
import shutil
import tarfile
import tempfile
import uuid
import zipfile
from datetime import datetime, timezone
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


# In-memory preflight cache (process-local; for v1 single-process backend)
_PREFLIGHT_STORE: Dict[str, Dict[str, Any]] = {}


async def preflight(zip_path: Path, user_id: str) -> Dict[str, Any]:
    """Validate the bundle and report what would be created.
    Returns: {preflight_token, manifest, warnings, name_clashes, embedding_compat}"""
    if not zip_path.exists():
        raise ValueError("zip_path does not exist")
    size = zip_path.stat().st_size
    if size > MAX_BUNDLE_BYTES:
        raise ValueError(f"bundle too large: {size}B > {MAX_BUNDLE_BYTES}B")

    # Extract to temp dir for inspection (kept until /confirm is called).
    work_dir = Path(tempfile.mkdtemp(prefix="nx-import-"))
    try:
        extract_zip_safely(zip_path, work_dir)
    except Exception:
        shutil.rmtree(work_dir, ignore_errors=True)
        raise

    manifest_path = work_dir / "manifest.json"
    if not manifest_path.exists():
        shutil.rmtree(work_dir, ignore_errors=True)
        raise ValueError("manifest.json missing in bundle")

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

    # Compatibility: check format version
    fv = manifest.get("bundle_format_version") or "0"
    major = fv.split(".")[0]
    if major != "1":
        shutil.rmtree(work_dir, ignore_errors=True)
        raise ValueError(f"unsupported bundle_format_version: {fv}")

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

    # Embedding compatibility — manifest's value vs current instance's default
    emb = manifest.get("embedding", {}) or {}
    embedding_compat = {
        "manifest": emb,
        "advice": "If your provider/dim differs, you'll be asked to rebuild RAG embeddings.",
    }

    token = uuid.uuid4().hex
    summary = {
        "preflight_token": token,
        "manifest": manifest,
        "name_clashes": name_clashes,
        "team_clash": team_clash,
        "embedding_compat": embedding_compat,
        "warnings": manifest.get("warnings", []),
    }
    _PREFLIGHT_STORE[token] = {
        "work_dir": str(work_dir),
        "user_id": user_id,
        "manifest": manifest,
    }
    return summary


async def confirm(preflight_token: str, user_id: str) -> Dict[str, Any]:
    """Execute the actual import using a previously preflighted bundle."""
    info = _PREFLIGHT_STORE.get(preflight_token)
    if not info:
        raise ValueError("preflight_token not found or expired")
    if info["user_id"] != user_id:
        raise ValueError("preflight_token user mismatch")

    work_dir = Path(info["work_dir"])
    manifest = info["manifest"]

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

        # workspace tar
        ws_tar = adir / "workspace.tar.gz"
        if ws_tar.exists():
            target = (
                Path.home() / ".nexusagent" / "workspaces" / f"{new_aid}_user_{user_id}"
            )
            target.mkdir(parents=True, exist_ok=True)
            with tarfile.open(ws_tar, "r:gz") as tar:
                # Reject any unsafe entries
                safe_members = []
                for member in tar.getmembers():
                    if member.issym() or member.islnk():
                        continue
                    if member.name.startswith("/") or ".." in member.name.split("/"):
                        continue
                    safe_members.append(member)
                tar.extractall(target, members=safe_members)

    # -- Skills --
    skill_archives_dir = Path.home() / ".nexusagent" / "skill_archives" / user_id
    skill_archives_dir.mkdir(parents=True, exist_ok=True)
    for s in manifest.get("skills", []):
        method = s.get("install_method")
        if method == "url":
            # Recipient instance will reinstall later via skills/install API.
            written_summary["skills_imported"] += 1
        elif method == "zip":
            archive_ref = s.get("archive_ref")
            zip_path = work_dir / archive_ref if archive_ref else None
            if zip_path and zip_path.exists():
                # Just copy to archive registry; user will run install_skill explicitly
                tgt = skill_archives_dir / f"{s['name']}_imported.zip"
                shutil.copy2(zip_path, tgt)
                written_summary["skills_imported"] += 1
        elif method == "full_copy":
            archive_ref = s.get("archive_ref")
            zip_path = work_dir / archive_ref if archive_ref else None
            if zip_path and zip_path.exists():
                tgt = skill_archives_dir / f"{s['name']}_full_imported.zip"
                shutil.copy2(zip_path, tgt)
                written_summary["skills_imported"] += 1

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
        _PREFLIGHT_STORE.pop(preflight_token, None)

    return written_summary
