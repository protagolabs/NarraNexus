"""
@file_name: _security.py
@author: Bin Liang
@date: 2026-10-30
@description: One greppable log line for every refused tenant-boundary crossing.

A request that tries to reach outside its own workspace — a traversing agent
id, a `..` file path, a bundle pointer that climbs into another tenant's tree —
is refused at several independent guards. Each used to log in its own words
(or not at all), so nothing downstream could tell "a user hit a stale link"
from "someone is probing the boundary". The selected guards below call
``log_boundary_violation``, and the line always starts with the same marker,
so a log watcher matches one fixed token instead of a list of phrasings.

Only call it where an honest client can never land. A false positive here
becomes an alert. Deliberately NOT wired: ``confine_entry`` (artifact
confinement). Legacy-layout artifact rows are refused there on every
freshness pass, so it would alert continuously on honest data.
"""
from __future__ import annotations

from typing import Optional
from urllib.parse import quote

from loguru import logger

BOUNDARY_MARKER = "[security.boundary]"
# Contract with the deploy repo's sentinel (abuse.boundary_violation): it
# matches this line from its first character, including the loguru head the
# text sink writes for THIS call site (`..._security:log_boundary_violation:<n>`).
# Renaming the module or the function, wrapping the logger call, or changing
# the line format (_setup._TEXT_FORMAT) silently stops the alert — update the
# sentinel's signatures.py in the same breath.

# The detail is caller-supplied and usually attacker-controlled (a path, an id).
# repr() keeps it on one line (newlines/control chars escaped, so a crafted
# value cannot forge a second log line); the cap bounds what one request can
# write into the log.
_DETAIL_MAX = 300


def _id_field(value: Optional[str]) -> str:
    """An id rendered as one whitespace-free token ("-" when unknown).

    Ids are normally plain tokens; a legacy one with a space (or `=`, or a
    control char) is percent-encoded so a ``key=value`` reader that splits on
    whitespace still sees exactly one token per field."""
    if not value:
        return "-"
    if any(ch.isspace() or ch == "=" or not ch.isprintable() for ch in value):
        return quote(value, safe="")
    return value


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
            (``bundle_import.preflight``, ``agent_files.path`` ...). Never
            user input.
        actor: user_id of the authenticated caller, when the guard knows it.
        owner: user_id that owns the record whose path was refused, when that
            differs from (or stands in for) the caller — e.g. an artifact row
            is owned by whoever registered its bad pointer.
        detail: The refused value or reason; sanitised to one bounded line.
    """
    logger.warning(
        f"{BOUNDARY_MARKER} surface={surface} actor={_id_field(actor)} "
        f"owner={_id_field(owner)} detail={_one_line(detail)}"
    )
