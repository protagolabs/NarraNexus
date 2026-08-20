"""
@file_name: lifecycle.py
@author: Bin Liang
@date: 2026-08-20
@description: The single definition of "is this agent still in its bootstrap
phase" — the signal that gates BOTH greeting writers.

`bootstrap_active` used to live inline in context_runtime (inject the
Bootstrap.md prompt) AND, once the step_1 greeting seed appeared, needed to be
recomputed identically in bootstrap/greeting_seed so the two writers stay in
lockstep. Copying it (workspace resolver + Bootstrap.md check + event-count
threshold, raw SQL and all) meant a silent drift risk: change the threshold
semantics or the workspace layout on one side and an agent past its bootstrap
phase gets re-greeted on a new narrative while the runtime thinks it's done.

This module owns that judgment. context_runtime and greeting_seed both call
`is_bootstrap_active`; context_runtime keeps its own auto-delete side-effect
(removing Bootstrap.md when over threshold) and uses the returned
`event_count`/`threshold`/`bootstrap_path` to do it — the helper decides, the
runtime acts. `bootstrap/` is the home of bootstrap semantics: the runtime LOGIC
module imports this judgment, not the reverse. (The only pre-existing
bootstrap→context_runtime edge is profiles.py importing the `context_runtime.prompts`
constants leaf — a leaf, no cycle; untouched here.)
"""
from __future__ import annotations

import os
from typing import TYPE_CHECKING, NamedTuple, Optional

from loguru import logger

from xyz_agent_context.bootstrap.profiles import auto_delete_threshold_from_meta
from xyz_agent_context.utils.workspace_paths import resolve_existing_workspace

if TYPE_CHECKING:
    from xyz_agent_context.utils.db.database import AsyncDatabaseClient


class BootstrapStatus(NamedTuple):
    """Result of a bootstrap-phase check.

    - active: Bootstrap.md present AND under the auto-delete threshold (the exact
      condition under which context_runtime injects the prompt and either writer
      greets).
    - present: Bootstrap.md exists on disk (distinguishes "no bootstrap" from
      "bootstrap present but expired" — the latter is when context_runtime
      auto-deletes).
    - event_count / threshold: for the caller's auto-delete decision + logging.
    - bootstrap_path: the resolved Bootstrap.md path (so the caller can remove it
      without re-resolving the workspace).
    """

    active: bool
    present: bool
    event_count: int
    threshold: Optional[int]
    bootstrap_path: str


async def is_bootstrap_active(
    db: "AsyncDatabaseClient",
    agent_id: str,
    owner_id: str,
    agent_metadata: Optional[dict],
) -> BootstrapStatus:
    """Whether `agent_id` is still bootstrapping for its owner.

    `owner_id` is the agent's creator (both callers pass created_by): the
    workspace is resolved under the owner, matching where Bootstrap.md is
    written. Caller does the owner-vs-current-user gate. Uses the READ-side
    workspace resolver so migrated (legacy flat) agents still resolve. A count
    query failure is treated as active (fail-open, matching the historical
    runtime behavior) but is now logged.
    """
    from xyz_agent_context.settings import settings

    bootstrap_path = os.path.join(
        str(resolve_existing_workspace(agent_id, owner_id, settings.base_working_path)),
        "Bootstrap.md",
    )
    if not os.path.isfile(bootstrap_path):
        return BootstrapStatus(False, False, 0, None, bootstrap_path)

    threshold = auto_delete_threshold_from_meta(agent_metadata)
    if threshold is None:
        # Semantic-only profile: never auto-deletes; active while the file exists.
        return BootstrapStatus(True, True, 0, None, bootstrap_path)

    try:
        rows = await db.execute(
            "SELECT COUNT(*) AS cnt FROM events WHERE agent_id = %s",
            (agent_id,),
            fetch=True,
        )
        event_count = rows[0]["cnt"] if rows else 0
    except Exception as e:  # noqa: BLE001 — fail-open like the runtime, but audible
        logger.warning(
            f"[bootstrap] event-count query failed for {agent_id}; treating as active: {e}"
        )
        event_count = 0

    return BootstrapStatus(event_count < threshold, True, event_count, threshold, bootstrap_path)
