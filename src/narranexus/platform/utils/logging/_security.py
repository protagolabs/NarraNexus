"""
@file_name: _security.py
@author: Bin Liang
@date: 2026-10-30
@description: One greppable log line for every refused tenant-boundary crossing.

A request that tries to reach outside its own workspace — a traversing agent
id, a `..` file path, a bundle pointer that climbs into another tenant's tree —
is refused at several independent guards. Each used to log in its own words
(or not at all), so nothing downstream could tell "a user hit a stale link"
from "someone is probing the boundary". Selected guards call
``log_boundary_violation`` (surfaces: agent_route.agent_id, agent_files.path,
agent_files.workspace_symlink, artifact.heal_symlink, bundle_import.preflight,
bundle_import.confirm), and the line always starts with the same marker,
so a log watcher matches one fixed token instead of a list of phrasings.

Only call it where an honest client can never land. A false positive here
becomes an alert. Deliberately NOT wired: ``confine_entry`` (artifact
confinement). Legacy-layout artifact rows are refused there on every
freshness pass, so it would alert continuously on honest data.
"""
from __future__ import annotations

import hashlib
import hmac
import os
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
#
# The line is SIGNED. A log stream mixes text the platform does not control
# (exception messages, uvicorn's own handler, print, subprocess output), so
# "this line starts with the right head" cannot prove the app wrote it. With
# NX_BOUNDARY_LOG_KEY set (the same value in the sentinel's environment), the
# line carries sig = HMAC-SHA256(key, surface|actor|owner|detail) truncated to
# 32 hex chars, and the sentinel attributes the alert to an account only when
# the signature verifies. Unset key -> `sig=-` (the sentinel then never names
# an account from the line). The signature does not cover time: a copied
# genuine line verifies again (replay), so it may support an alert, never an
# automatic enforcement decision.
BOUNDARY_KEY_ENV = "NX_BOUNDARY_LOG_KEY"
_SIG_HEX = 32


def boundary_signature(surface: str, actor: str, owner: str, detail: str) -> str:
    """The `sig=` value for already-rendered fields; "-" without a key.

    Inputs are the exact tokens written to the line, so the verifier can
    recompute it from the line alone."""
    key = os.environ.get(BOUNDARY_KEY_ENV, "").strip()  # same as the sentinel's read
    if not key:
        return "-"
    msg = "|".join((surface, actor, owner, detail)).encode("utf-8")
    return hmac.new(key.encode("utf-8"), msg, hashlib.sha256).hexdigest()[:_SIG_HEX]

# The detail is caller-supplied and usually attacker-controlled (a path, an id).
# repr() keeps it on one line (newlines/control chars escaped, so a crafted
# value cannot forge a second log line); the cap bounds what one request can
# write into the log.
_DETAIL_MAX = 300


def _id_field(value: Optional[str]) -> str:
    """An id rendered as one whitespace-free token ("-" when unknown).

    Ids are normally plain tokens; a legacy one with a space (or `=`, `|`, or
    a control char) is percent-encoded — `|` because it is the signature's
    field separator, so a raw one would let two field splits sign alike — so a ``key=value`` reader that splits on
    whitespace still sees exactly one token per field."""
    if not value:
        return "-"
    if any(ch.isspace() or ch in "=|" or not ch.isprintable() for ch in value):
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
    actor_f, owner_f, detail_f = _id_field(actor), _id_field(owner), _one_line(detail)
    sig = boundary_signature(surface, actor_f, owner_f, detail_f)
    logger.warning(
        f"{BOUNDARY_MARKER} surface={surface} actor={actor_f} "
        f"owner={owner_f} sig={sig} detail={detail_f}"
    )
