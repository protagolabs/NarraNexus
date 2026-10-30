"""
@file_name: skill_sync_service.py
@author: NetMind.AI
@date: 2026-07-21
@description: Reconciler keeping skill_installations (audit DB) in sync with
the filesystem truth (skills/ + .skill_meta.json).

Users cannot be prevented from hand-editing skills/ (rm -rf, cp -r, manual
edits), so the audit table is a FOLLOWER: this service only ever writes DB
rows and never touches user files. Idempotent — every pass computes the
target state and upserts it.

Drift handling per workspace skill:
- on disk, no DB row              -> upsert (source from .skill_meta.json,
                                     else "manual")
- in DB "installed", not on disk  -> status = external_removed (row kept)
- content hash differs from meta  -> status = modified (unmanaged edit)
- under skills/.disabled/         -> status = disabled
- back on disk after removal      -> status returns to installed

Triggers: run_forever loops on a fixed interval (SKILL_SYNC_INTERVAL_SECONDS,
default 1800), doing its FIRST reconcile immediately (so backend startup does
not block on it — the lifespan launches this as a background task). Setting
the interval to 0 (or below) DISABLES the reconciler entirely — no periodic
loop AND no startup pass; tests use this to opt out. Lifecycle events go to
the log (started/healed/error per pass — incident lesson #4/#5).
"""

import asyncio
import os
import stat
from pathlib import Path
from typing import Dict, Optional

from loguru import logger

from narranexus.platform.marketplace._skill_marketplace_impl.install_pipeline import compute_content_hash
from narranexus.platform.repository.skill_installation_repository import (
    SkillInstallationRepository,
)

DEFAULT_INTERVAL_SECONDS = 1800


class SkillSyncService:
    def __init__(self, db_client):
        self._db = db_client
        self.repo = SkillInstallationRepository(db_client)

    # -- single workspace ----------------------------------------------------

    async def reconcile_workspace(self, agent_id: str, user_id: str) -> Dict[str, int]:
        """Reconcile one workspace; returns counters for observability."""
        from narranexus.platform.utils.plugin_services import skill_workspace

        module = skill_workspace(agent_id, user_id)
        stats = {"added": 0, "external_removed": 0, "modified": 0, "disabled": 0, "restored": 0}
        skills_dir = module.skills_dir
        if skills_dir is None:
            return stats

        # The whole disk side (listings, meta reads, content hashes) runs off
        # the event loop: the periodic reconcile covers every tenant, and one
        # large skills tree must not stall the workers' loop.
        disk_state, disk_meta = await asyncio.to_thread(self._scan_disk, module, skills_dir)

        rows = {r.skill_id: r for r in await self.repo.list_for_workspace(agent_id, user_id)}

        for name, target_status in disk_state.items():
            row = rows.get(name)
            if row is None:
                meta = disk_meta.get(name, {})
                await self.repo.upsert_event(
                    agent_id,
                    user_id,
                    name,
                    version=meta.get("version"),
                    source_type=meta.get("source_type") or "manual",
                    source_url=meta.get("source_url"),
                    package_hash=meta.get("hash"),
                    status=target_status,
                    last_event="reconcile",
                )
                stats["added"] += 1
            elif row.status != target_status:
                await self.repo.mark_status(
                    agent_id, user_id, name, status=target_status, last_event="reconcile"
                )
                key = {
                    "modified": "modified",
                    "disabled": "disabled",
                    "installed": "restored",
                }.get(target_status, "restored")
                stats[key] += 1

        for name, row in rows.items():
            if name not in disk_state and row.status not in ("uninstalled", "external_removed"):
                await self.repo.mark_status(
                    agent_id, user_id, name, status="external_removed", last_event="reconcile"
                )
                stats["external_removed"] += 1

        # Reconcile changed what this agent can do (skills added on disk,
        # others removed behind our back), and capabilities are what peers
        # search on — so the discovery row is stale until it is recomputed.
        if any(stats.values()):
            from narranexus.platform.message_bus.agent_discovery_sync import (
                sync_agent_discovery,
            )
            await sync_agent_discovery(self._db, agent_id)

        return stats

    @classmethod
    def _scan_disk(cls, module, skills_dir: Path) -> tuple:
        """``(name -> target status, name -> meta)`` for one skills dir (sync).

        Listed through the link-free walk below the base: `skills` and
        `.disabled` sit in the owner's mount, and a link swapped in for either
        must not make another tenant's skills (names, meta, hash) this agent's.
        """
        disk_state: Dict[str, str] = {}
        disk_meta: Dict[str, dict] = {}
        for name in cls._confined_subdirs(skills_dir):
            if not name.startswith("."):
                disk_state[name] = cls._live_status(module, skills_dir / name)
                disk_meta[name] = module.read_skill_meta(name)
        for name in cls._confined_subdirs(skills_dir / ".disabled"):
            disk_state.setdefault(name, "disabled")
        return disk_state, disk_meta

    @staticmethod
    def _confined_subdirs(directory: Path) -> list:
        """Names of the REAL subdirectories of ``directory`` (links are not
        directories here); empty when it is missing, a link, or unreadable."""
        from narranexus.platform.settings import settings
        from narranexus.platform.utils.fs import confined_io

        try:
            root, rel = confined_io.split_under_base(str(directory), settings.base_working_path)
            return sorted(i.name for i in confined_io.list_dir(root, rel) if i.is_dir)
        except (OSError, ValueError):
            return []

    @staticmethod
    def _live_status(module, skill_dir: Path) -> str:
        meta = module.read_skill_meta(skill_dir.name)
        recorded_hash = meta.get("content_hash")
        if recorded_hash:
            try:
                if compute_content_hash(skill_dir) != recorded_hash:
                    return "modified"
            except (OSError, ValueError) as exc:  # ConfinedPathError is a ValueError
                logger.warning(f"Reconcile: cannot hash {skill_dir}: {exc}")
        return "installed"

    # -- whole host ----------------------------------------------------------

    @staticmethod
    def _skill_workspaces(base_working_path: str) -> list:
        """``(user, agent)`` pairs under the base that have a real ``skills``
        dir (sync — every tenant is listed, so the caller runs it in a thread).

        Each level through the link-free walk: an `agent_dir` the tenant
        replaced with a link to another tenant's workspace is not a workspace
        here (it would be reconciled — read and recorded — as the attacker's
        agent).
        """
        from narranexus.platform.utils.fs import confined_io

        try:
            base_root = os.path.realpath(base_working_path)
            user_names = sorted(i.name for i in confined_io.list_dir(base_root, "") if i.is_dir)
        except (OSError, ValueError):
            return []
        out = []
        for user_name in user_names:
            if user_name.startswith("."):
                continue
            try:
                agent_names = sorted(i.name for i in confined_io.list_dir(base_root, user_name) if i.is_dir)
            except (OSError, ValueError):
                continue
            for agent_name in agent_names:
                if agent_name.startswith("."):
                    continue
                try:
                    st = confined_io.lstat(base_root, f"{user_name}/{agent_name}/skills")
                except (OSError, ValueError):
                    continue
                if stat.S_ISDIR(st.st_mode):
                    out.append((user_name, agent_name))
        return out

    async def reconcile_all(self) -> Dict[str, int]:
        """Walk every workspace under base_working_path (nested layout
        {user_id}/{agent_id}) and reconcile those that have a skills dir."""
        from narranexus.platform.settings import settings

        totals = {"workspaces": 0, "added": 0, "external_removed": 0,
                  "modified": 0, "disabled": 0, "restored": 0}
        for user_name, agent_name in await asyncio.to_thread(self._skill_workspaces, settings.base_working_path):
            try:
                stats = await self.reconcile_workspace(agent_name, user_name)
            except Exception as exc:
                logger.error(
                    f"Reconcile failed for workspace {user_name}/{agent_name}: {exc}"
                )
                continue
            totals["workspaces"] += 1
            for key in ("added", "external_removed", "modified", "disabled", "restored"):
                totals[key] += stats[key]
        return totals

    async def run_forever(self, interval_seconds: Optional[int] = None) -> None:
        interval = (
            interval_seconds
            if interval_seconds is not None
            else int(os.environ.get("SKILL_SYNC_INTERVAL_SECONDS", DEFAULT_INTERVAL_SECONDS))
        )
        if interval <= 0:
            logger.info("SkillSyncService: disabled (interval <= 0)")
            return
        logger.info(f"SkillSyncService: started (interval {interval}s)")
        while True:
            try:
                totals = await self.reconcile_all()
                if any(totals[k] for k in totals if k != "workspaces"):
                    logger.info(f"SkillSyncService: drift healed {totals}")
                else:
                    logger.debug(f"SkillSyncService: pass clean {totals}")
            except asyncio.CancelledError:
                logger.info("SkillSyncService: stopped")
                raise
            except Exception as exc:
                logger.error(f"SkillSyncService: pass failed: {exc}")
            await asyncio.sleep(interval)
