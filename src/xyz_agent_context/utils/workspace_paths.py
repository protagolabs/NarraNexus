"""
@file_name: workspace_paths.py
@author:
@date: 2026-06-17
@description: Single source of truth for an agent's on-disk workspace layout.

Historically the layout ``{base_working_path}/{agent_id}_{user_id}`` was
hardcoded as ``f"{agent_id}_{user_id}"`` in ~10 places (step_3, bundle,
bootstrap, skill_module, attachment_storage, ...). That made it
impossible to change the layout without hunting every call site.

This module centralizes it. Today it returns the legacy FLAT name so the
conversion is behaviour-identical. The next step flips
``_LAYOUT`` to the per-user nested form ``{user_id}/{agent_id}`` — which
is what lets a per-user Executor container bind-mount only
``{base}/{user_id}`` and thereby see ONLY that user's agents (cross-user
file isolation by mount, no uid tricks needed). When that flip happens,
ONLY this module changes (plus a one-off data migration); every call
site already routes through here.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Optional

# Layout selector. "flat" = legacy ``{agent_id}_{user_id}`` (current on
# disk). "nested" = ``{user_id}/{agent_id}`` (per-user mount isolation).
# Flip to "nested" together with the data migration — never before, or
# running agents lose their workspace.
_LAYOUT = "nested"


def agent_workspace_relpath(agent_id: str, user_id: str) -> str:
    """Workspace path of one agent, RELATIVE to base_working_path.

    A POSIX-style relative path (may contain a ``/`` once the layout is
    nested). Callers that need an absolute path use
    :func:`agent_workspace_path`.
    """
    if _LAYOUT == "nested":
        return f"{user_id}/{agent_id}"
    return f"{agent_id}_{user_id}"


def agent_workspace_path(
    agent_id: str, user_id: str, base: Optional[str] = None
) -> Path:
    """Absolute workspace path for one agent.

    Args:
        base: base working dir; defaults to ``settings.base_working_path``.
    """
    if base is None:
        from xyz_agent_context.settings import settings
        base = settings.base_working_path
    return Path(base) / agent_workspace_relpath(agent_id, user_id)


# ---------------------------------------------------------------------------
# One-off migration: flat ``{agent_id}_{user_id}`` → nested ``{user_id}/{agent_id}``
# ---------------------------------------------------------------------------

_LEGACY_INFIX = "user_"


def _parse_flat_dirname(
    name: str, known_user_ids: set[str]
) -> Optional[tuple[str, str]]:
    """Parse a legacy flat workspace dir name into (agent_id, user_id).

    Agent ids are ``agent_<hex>`` (single token, no internal ``_``), so the
    name is ``agent_<hex>_<rest>``. ``<rest>`` is AMBIGUOUS: it could be the
    user_id directly (canonical ``{agent}_{user}``) OR the legacy
    ``{agent}_user_{user}`` infix form — e.g. ``agent_x_user_binliang`` is
    user ``binliang`` (infix), NOT user ``user_binliang``. Dir names alone
    cannot disambiguate, so we resolve against the authoritative set of
    real user ids from the DB.

    Returns None when neither interpretation matches a known user (an
    orphan / unknown dir — never guessed, left in place by the caller).
    """
    if not name.startswith("agent_"):
        return None
    parts = name.split("_")
    if len(parts) < 3:
        return None
    agent_id = f"{parts[0]}_{parts[1]}"
    rest = "_".join(parts[2:])
    if rest in known_user_ids:
        return agent_id, rest
    if rest.startswith(_LEGACY_INFIX):
        candidate = rest[len(_LEGACY_INFIX):]
        if candidate in known_user_ids:
            return agent_id, candidate
    return None


def migrate_flat_to_nested(
    base: str, known_user_ids: set[str], dry_run: bool = False
) -> dict:
    """Rename legacy flat workspace dirs to the nested per-user layout.

    ``known_user_ids`` is the authoritative set of real user ids (from the
    DB ``users`` table) — REQUIRED to disambiguate the legacy ``_user_``
    infix form from a real user id that happens to start with ``user_``.

    Idempotent and non-destructive:
      - only top-level ``agent_*_*`` dirs whose owner resolves to a known
        user are moved;
      - if the nested target already exists, the flat dir is left in place
        (reported as a conflict — never overwritten / deleted);
      - dirs that don't resolve to a known user are left in place
        (reported as ``unknown``), never guessed;
      - already-nested user dirs are skipped.

    Run once at deploy (see ``scripts/migrate_workspace_layout.py``) BEFORE
    flipping ``_LAYOUT`` to "nested". Returns a report dict.
    """
    root = Path(base)
    report: dict = {"moved": [], "skipped": [], "conflicts": [], "unknown": []}
    if not root.is_dir():
        return report
    for entry in sorted(root.iterdir()):
        if not entry.is_dir():
            continue
        if not entry.name.startswith("agent_"):
            report["skipped"].append(entry.name)
            continue
        parsed = _parse_flat_dirname(entry.name, known_user_ids)
        if parsed is None:
            report["unknown"].append(entry.name)
            continue
        agent_id, user_id = parsed
        target = root / user_id / agent_id
        if target.exists():
            report["conflicts"].append(entry.name)
            continue
        report["moved"].append((entry.name, f"{user_id}/{agent_id}"))
        if not dry_run:
            target.parent.mkdir(parents=True, exist_ok=True)
            os.rename(entry, target)
    return report
