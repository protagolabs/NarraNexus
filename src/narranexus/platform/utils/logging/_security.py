"""
@file_name: _security.py
@author: Bin Liang
@date: 2026-10-30
@description: One greppable log line for every refused tenant-boundary crossing.

A request that tries to reach outside its own workspace — a traversing agent
id, a `..` file path, an artifact pointer that resolves under another tenant —
is refused at several independent guards. Each guard used to log in its own
words (or not at all), so nothing downstream could tell "a user hit a stale
link" from "someone is probing the boundary". Every guard now calls
``log_boundary_violation`` and the line always starts with the same marker, so
a log watcher matches one fixed token instead of a list of phrasings.

Only call it where an honest client can never land: the guards it is wired
into reject shapes the product UI never produces (traversal segments, paths
resolving outside the owner's roots). A false positive here becomes an alert.
"""
from __future__ import annotations

from typing import Optional

from loguru import logger

BOUNDARY_MARKER = "[security.boundary]"

# The detail is caller-supplied and usually attacker-controlled (a path, an id).
# repr() keeps it on one line (newlines/control chars escaped, so a crafted
# value cannot forge a second log line); the cap bounds what one request can
# write into the log.
_DETAIL_MAX = 300


def _one_line(value: str) -> str:
    text = repr(value)
    if len(text) > _DETAIL_MAX:
        text = text[:_DETAIL_MAX] + "...(truncated)"
    return text


def log_boundary_violation(
    surface: str,
    *,
    actor: Optional[str] = None,
    owner: Optional[str] = None,
    detail: str = "",
) -> None:
    """Emit one WARNING line for a refused attempt to cross a tenant boundary.

    Args:
        surface: Fixed, code-chosen name of the guard that refused
            (``bundle_import.preflight``, ``artifact.confine`` ...). Never
            user input.
        actor: user_id of the authenticated caller, when the guard knows it.
        owner: user_id that owns the record whose path was refused, when that
            differs from (or stands in for) the caller — e.g. an artifact row
            is owned by whoever registered its bad pointer.
        detail: The refused value or reason; sanitised to one bounded line.
    """
    logger.warning(
        f"{BOUNDARY_MARKER} surface={surface} actor={actor or '-'} "
        f"owner={owner or '-'} detail={_one_line(detail)}"
    )
