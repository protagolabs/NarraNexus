"""
@file_name: _narramessenger_service.py
@date: 2026-07-02
@description: Bind-flow driver for NarraMessenger — shared by the ``narra_bind``
MCP tool and the ``/api/narramessenger/bind`` backend route.

Drives the platform's **Direct Matrix** bind deterministically from a single
bind token (the public token inside a link like
``https://api.netmind.chat/<token>/setup-guide.md``):

  1. ``report-profile`` (the agent's name + bio) to advance the bind session.
  2. Re-fetch the setup-guide until its status reaches ``waiting_connection``
     (a bounded poll — the guide served right after report-profile can lag).
  3. ``POST /bind-agent/claim-credentials?token=<bind_token>`` — the one-time
     claim that returns the runtime **bearer** (``secret_token``), the
     **Matrix access token**, the Matrix user id and the homeserver URL as
     JSON. The guide itself never prints these (since 2026-11).
  4. ``POST /bind-agent/runtime-ready?token=<bind_token>`` with
     ``{"platform": "nexus"}`` — completes the Direct Matrix bind, tags the
     agent's origin platform on the NarraMessenger side, and returns the
     initial ``bind_room_id`` where the owner will send the first test
     message.
  5. Upsert the credential row with ``connection_mode='matrix'`` and both
     tokens → :class:`MatrixTrigger`'s credential watcher auto-starts syncing.

Direct Matrix is the DEFAULT bind path per the NarraMessenger guide's
Runtime Selection Rule ("Do not ask the creator to choose Matrix vs
Gateway"). Gateway (Polling) is a fallback for environments that cannot
host a Matrix client — supported by NarraMessenger via
``POST /api/agent-gateway/connect`` on the same bearer, but not exercised
by this bind flow anymore. If the owner needs Gateway later, they can hit
that endpoint directly with the stored bearer.

Egress gate (cloud): the bind link is pasted by the user and the homeserver URL
comes back from that link's host in the claim response, so BOTH are attacker-
influenced server-side fetch targets. Before any request the link's base URL
passes the shared ``assert_public_http_url_in_cloud`` gate, and the parsed homeserver
passes it before ``runtime-ready`` completes the remote bind or anything is
persisted. Redirects are never followed (a public host could 302 into the
cluster). In cloud the session's connector is pinned as well
(``safe_http.egress_connector``) so the address actually dialled is the one
validated, and ``*_PROXY`` env is ignored. Local/desktop skips the gate (single
trusted user).

The claim is accepted once per bind link (NarraMessenger allows a retry within
10 minutes), so it is only made once the guide says ``waiting_connection``, and
its response is never logged. A session that never gets there returns an error
naming the guide's bind status rather than guessing.
"""

from __future__ import annotations

import asyncio
import re
import time
from typing import Any, Optional, Tuple

import aiohttp
from loguru import logger

from narranexus.platform.utils import deployment_mode
from narranexus.platform.utils.safe_http import egress_connector
from narranexus.platform.utils.url_safety import UnsafeUrlError, assert_public_http_url_in_cloud

from ._narramessenger_credential_manager import (
    NarramessengerCredential,
    NarramessengerCredentialManager,
)

_DEFAULT_BASE = "https://api.netmind.chat"

# Upstream error codes we act on or may show the user verbatim. Anything else
# the host returns in ``error`` / ``message`` stays in the log: the bind host is
# user-chosen, so its free text must never be reflected back.
_KNOWN_ERROR_CODES = frozenset({"WRONG_STATE"})

# Origin tag sent to NarraMessenger on ``runtime-ready`` so their side can
# label which platform an agent was bound from. Their field defaults to
# ``"default"`` when the key is absent — which is exactly what every agent we
# bound looked like before this was added.
PLATFORM_TAG = "nexus"

# The bind state machine's position as the guide renders it: the pre-profile
# guide says `- **Status**: `waiting_profile`` under "Bind Session", the
# connected one `> **Bind Flow Status**: `waiting_connection``. First match
# wins (both render at the top). Constrained to a bare identifier so the value
# can be logged and named to the user without reflecting free text.
_FLOW_STATUS_RE = re.compile(
    r"\*\*(?:Bind Flow )?Status\*\*\W{0,4}`?([A-Za-z][A-Za-z0-9_]{0,39})"
)
# Statuses at which the runtime credentials can be claimed. `connected` is a
# session whose bind already completed: the claim decides (it is refused once
# the claim window has passed, and the user is told to make a new link).
_CLAIMABLE_STATUSES = frozenset({"waiting_connection", "connected"})
# Fields the claim response must carry, each a non-empty string.
_CLAIM_FIELDS = ("secret_token", "matrix_user_id", "matrix_access_token", "homeserver_url")
# `## Heading` lines — the guide's section layout, logged when the session
# never reaches the credential step so an upstream format change is visible in logs. Fenced code
# blocks are stripped first (their `# comment` lines would match, and the
# guide's examples carry tokens), and only plain-prose headings are kept.
_FENCE_RE = re.compile(r"(```|~~~).*?(\1|\Z)", re.DOTALL)
_HEADING_RE = re.compile(r"^#{1,3}\s+(.+?)\s*$", re.MULTILINE)
_SAFE_HEADING_RE = re.compile(r"[A-Za-z][A-Za-z0-9 ()&'/,.-]{0,59}")
_TOKENISH_RE = re.compile(r"syt_|://|=|[A-Za-z0-9]{16,}|(?:\D*\d){3}")

# report-profile advances the session to ``waiting_connection``, but the guide
# served right after it can still be the previous state, so the re-fetch waits
# for a claimable status instead of reading the guide once. Delays in seconds
# between attempts.
_STATUS_DELAYS: Tuple[float, ...] = (0.5, 1.0, 1.5, 2.0, 2.5, 3.0)
# Ceiling on the status wait (its fetches included), measured from the start of
# do_bind. The cloud MCP bind reaches this code through HttpStore
# (narranexus/platform/module_system/data_access/channel_store.py), whose HTTP
# call has a 20 s budget; 12 s leaves ~8 s for the claim, runtime-ready and the
# upsert. It bounds the wait only: the claim and runtime-ready still run under
# the session's 25 s timeout, so a late status followed by slow calls can still
# exceed 20 s and show the user a timeout for a bind that landed. Neither call is
# cut short: cancelling runtime-ready mid-request can leave the remote side
# bound and ours not. A re-paste within the claim's 10-minute retry window
# recovers either way — the guide then says `connected` (or still
# `waiting_connection`) and the claim is retried.
_STATUS_DEADLINE_S = 12.0
# Indirections so tests can run the wait without real sleeps or a real clock.
_sleep = asyncio.sleep
_monotonic = time.monotonic

# `.../<token>/setup-guide...` inside a pasted link
_URL_TOKEN_RE = re.compile(
    r"https?://([A-Za-z0-9_.\-]+)/([A-Za-z0-9_\-]+)/setup-guide", re.IGNORECASE
)


def _parse_bind_command(bind_command: str) -> Tuple[str, str]:
    """Extract ``(token, backend_base_url)`` from a pasted command/link.

    Accepts a full URL (``https://host/<token>/setup-guide.md``) or a looser
    ``narra bind <token>`` / bare-token paste. Defaults the base URL to
    ``api.netmind.chat`` when only a bare token is given.
    """
    s = (bind_command or "").strip()
    m = _URL_TOKEN_RE.search(s)
    if m:
        host, token = m.group(1), m.group(2)
        return token, f"https://{host}"
    # Looser paste: take the last token-looking word that isn't noise.
    words = re.findall(r"[A-Za-z0-9_\-]{4,64}", s)
    skip = {"narra", "bind", "agent", "agents", "setup", "guide", "https",
            "http", "com", "net", "chat", "md", "the", "token", "link"}
    cand = [w for w in words if w.lower() not in skip]
    return (cand[-1] if cand else ""), _DEFAULT_BASE


async def _agent_profile(db, agent_id: str) -> Tuple[str, str]:
    """Look up the agent's display name + a short bio for ``report-profile``."""
    agent = None
    try:
        agent = await db.get_one("agents", {"agent_id": agent_id})
    except Exception:  # noqa: BLE001
        agent = None
    name = ""
    bio = ""
    if agent:
        name = str(agent.get("name") or agent.get("agent_name") or "")
        bio = str(agent.get("description") or agent.get("bio") or "")
    name = (name.strip() or "NarraNexus Agent")[:30]
    bio = (bio.strip() or "A NarraNexus agent reachable on NarraMessenger.")[:200]
    return name, bio


def _flow_status(md: str) -> str:
    """The guide's bind status as a bare identifier, or "" when it shows none."""
    fm = _FLOW_STATUS_RE.search(md or "")
    return fm.group(1) if fm else ""


def _parse_claim(data: dict) -> Optional[dict[str, str]]:
    """The claim response's credentials, or None when any field is missing.

    - ``secret_token``: the Narra agent bearer, used for every
      ``/api/agent-runtime/*`` call (including the authorize-event gate).
    - ``matrix_access_token``: used ONLY for Matrix HTTP API calls. Never
      mix with the Narra bearer — Matrix rejects it with ``M_UNKNOWN_TOKEN``.
    - ``homeserver_url``: the Matrix homeserver (egress-gated by the caller).
    - ``matrix_user_id``: the agent's own MXID.
    """
    out: dict[str, str] = {}
    for key in _CLAIM_FIELDS:
        value = data.get(key)
        if not isinstance(value, str) or not value.strip():
            return None
        out[key] = value.strip()
    return out


def _guide_shape(md: str) -> str:
    """A secret-free description of a guide for the log: size, status, sections.

    Never includes a value from the guide body — only the length, the
    identifier-shaped flow status and the prose section headings (fenced
    blocks stripped, headings limited to a plain-text alphabet, anything
    token-shaped dropped).
    """
    if not md:
        return "empty"
    status = _flow_status(md) or "?"
    prose = _FENCE_RE.sub("", md)
    headings = [
        h for h in _HEADING_RE.findall(prose)
        if _SAFE_HEADING_RE.fullmatch(h) and not _TOKENISH_RE.search(h)
    ][:12]
    return f"len={len(md)} flow_status={status} headings={headings}"


def _fail(agent_id: str, step: str, message: str, detail: str = "") -> dict[str, Any]:
    """Log a bind failure with its step, then return the user-facing envelope.

    Every ``do_bind`` failure goes through here so the server log says which
    step failed and why; before, most branches returned silently and a failed
    bind left only an HTTP 200 in the access log.
    """
    logger.warning(
        f"[narramessenger:{agent_id}] bind failed at {step}"
        + (f": {detail}" if detail else "")
    )
    return {"success": False, "error": message}


async def _is_allowed_target(url: str) -> bool:
    """Cloud-only SSRF egress gate for a bind-time fetch target.

    False on ANY failure to decide (unsafe, unresolvable, malformed) — fail
    closed. The reason goes to the log only; callers return a generic message
    so the gate never becomes an oracle for the internal network.
    """
    try:
        await assert_public_http_url_in_cloud(url)
    except Exception as e:  # noqa: BLE001 — any decision failure fails closed
        logger.warning(f"[narramessenger] bind target refused by egress gate ({type(e).__name__}): {e}")
        return False
    return True


async def _get_text(session: aiohttp.ClientSession, url: str) -> str:
    async with session.get(url, allow_redirects=False) as resp:
        if 200 <= resp.status < 300:
            return await resp.text()
        if 300 <= resp.status < 400:
            # Redirects are refused, and the caller maps "" to "token not found";
            # log the status so an upstream redirect is not misread as a bad
            # token. Never log or surface Location (upstream-controlled).
            logger.warning(f"[narramessenger] bind GET refused a {resp.status} redirect")
        return ""


async def _post_json(
    session: aiohttp.ClientSession, url: str, body: Optional[dict], bearer: str = ""
) -> dict:
    headers = {"Content-Type": "application/json"}
    if bearer:
        headers["Authorization"] = f"Bearer {bearer}"
    kwargs: dict[str, Any] = {"headers": headers, "allow_redirects": False}
    if body is not None:
        kwargs["json"] = body
    async with session.post(url, **kwargs) as resp:
        try:
            data = await resp.json()
        except Exception:  # noqa: BLE001
            data = {}
        if 200 <= resp.status < 300:
            return {"ok": True, "data": data if isinstance(data, dict) else {}}
        # Raw upstream code/text, for control flow only (WRONG_STATE check).
        # Never shown as-is — `_step_failure` builds the user-facing text.
        err = ""
        if isinstance(data, dict):
            err = str(data.get("error") or data.get("message") or "")
        return {"ok": False, "status": resp.status, "error": err}


def _step_failure(step: str, result: dict) -> str:
    """User-facing failure for a bind step: status code + whitelisted code only.

    The single place a step result becomes user text; the raw upstream string
    goes to the log unless it is a known code.
    """
    raw = str(result.get("error") or "")
    code = raw if raw in _KNOWN_ERROR_CODES else ""
    if raw and not code:
        logger.info(f"[narramessenger] {step} upstream error: {raw[:200]}")
    status = result.get("status")
    detail = f"HTTP {status}" if status else "no response"
    return f"{step} failed ({detail}{', ' + code if code else ''})"


async def _await_claimable(
    session: aiohttp.ClientSession, guide_url: str, deadline: float, agent_id: str
) -> Tuple[str, str]:
    """Re-fetch the setup guide until its status is claimable, or the wait runs out.

    report-profile advances the bind session, but the guide served right after
    it may still render the previous state; reading it once turned that lag
    into a failed bind. Stops at ``deadline`` (a ``_monotonic`` time). Returns
    the last non-empty guide and its status ("" if every re-fetch came back
    empty).
    """
    guide = ""
    status = ""
    reads = 0
    ended = "attempts"
    for delay in _STATUS_DELAYS:
        remaining = deadline - _monotonic()
        if remaining <= delay:
            # Not enough time left to sleep and still read: give the time back.
            ended = "deadline"
            break
        await _sleep(delay)
        remaining = deadline - _monotonic()
        if remaining <= 0:
            ended = "deadline"
            break
        reads += 1
        try:
            # A read is side-effect free, so cutting it off at the deadline is safe.
            text = await asyncio.wait_for(_get_text(session, guide_url), remaining)
        except asyncio.TimeoutError:
            ended = "read timeout"
            break
        if not text:
            # Keep the last guide we did get: its status is what the user is told.
            continue
        guide = text
        status = _flow_status(guide)
        if status in _CLAIMABLE_STATUSES:
            return guide, status
    logger.info(f"[narramessenger:{agent_id}] status wait ended after {reads} re-read(s): {ended}")
    return guide, status


def _not_claimable_message(flow_status: str) -> str:
    """User text for a bind session that never reached the credential step."""
    where = f" (bind status: {flow_status})" if flow_status else ""
    return (
        "NarraMessenger has not released the agent credential yet"
        f"{where}. If NarraMessenger asks you to confirm the bind, confirm it "
        "there, then paste the same link again; otherwise generate a new bind "
        "link under My Agents → Bind Agents."
    )


def _claim_hint(status: Optional[int]) -> str:
    """What the user should do after a failed or incomplete claim.

    A 4xx means NarraMessenger refused this link's claim (already used past
    its retry window, expired, unknown): only a new link helps. Anything else
    (5xx, 429, no response, a malformed body) may be transient, and the same
    link can be claimed again for 10 minutes.
    """
    if status is not None and 400 <= status < 500 and status != 429:
        return (
            " The credential for a bind link can be claimed only once. Generate "
            "a new bind link under My Agents → Bind Agents and paste it here."
        )
    return (
        " Paste the same link again within 10 minutes; after that, generate a "
        "new bind link under My Agents → Bind Agents."
    )


async def do_bind(db, agent_id: str, bind_command: str) -> dict[str, Any]:
    """Drive the Direct Matrix bind from a pasted bind command/link.

    Returns ``{"success": True, "data": {...}}`` or
    ``{"success": False, "error": <message>}``. See module docstring. Every
    failure is logged with the step it happened at (``_fail``).
    """
    started = _monotonic()
    token, base_url = _parse_bind_command(bind_command)
    if not token:
        return _fail(
            agent_id, "parse-link",
            "Could not find a bind token in the pasted text. Paste the "
            "full bind link shown under My Agents → Bind Agents.",
        )
    name, bio = await _agent_profile(db, agent_id)
    base = base_url.rstrip("/")
    if not await _is_allowed_target(base):
        return _fail(
            agent_id, "egress-gate",
            "The bind link does not point at a public NarraMessenger "
            "host. Re-copy the bind link from My Agents → Bind Agents.",
        )
    guide_url = f"{base}/{token}/setup-guide.md"

    try:
        # Cloud pins every dial (the gate above resolved the name separately —
        # DNS rebinding) and ignores *_PROXY env: a proxy resolves the name
        # itself, bypassing the pin. Local keeps trust_env for desktop users
        # behind a system proxy.
        async with aiohttp.ClientSession(
            connector=egress_connector(),
            trust_env=not deployment_mode.is_cloud_mode(),
            timeout=aiohttp.ClientTimeout(total=25),
        ) as session:
            guide = await _get_text(session, guide_url)
            if not guide:
                return _fail(
                    agent_id, "setup-guide",
                    "Bind token not found or expired (could not fetch the "
                    "setup guide). Re-copy the bind link from My Agents → Bind Agents.",
                )
            status = _flow_status(guide)

            # Advance the session if it is not at the credential step yet.
            if status not in _CLAIMABLE_STATUSES:
                rp = await _post_json(
                    session,
                    f"{base}/bind-agent/report-profile?token={token}",
                    {"name": name, "bio": bio},
                )
                # WRONG_STATE just means we're already past the profile step.
                if not rp.get("ok") and rp.get("error") not in ("WRONG_STATE", ""):
                    return _fail(
                        agent_id, "report-profile",
                        _step_failure("report-profile", rp),
                        f"HTTP {rp.get('status')}",
                    )
                if not rp.get("ok"):
                    # rp['error'] is text from the user-chosen host: bounded and repr'd.
                    logger.info(
                        f"[narramessenger:{agent_id}] report-profile answered "
                        f"HTTP {rp.get('status')} {str(rp.get('error') or '(no code)')[:100]!r}; "
                        "treating the session as past the profile step"
                    )
                waited_guide, waited_status = await _await_claimable(
                    session, guide_url, started + _STATUS_DEADLINE_S, agent_id
                )
                if waited_guide:
                    guide, status = waited_guide, waited_status

            if status not in _CLAIMABLE_STATUSES:
                return _fail(
                    agent_id, "await-connection",
                    _not_claimable_message(status),
                    _guide_shape(guide),
                )

            # One-time credential claim. The response carries every secret of
            # this agent: it is parsed in memory and never logged.
            claim = await _post_json(
                session, f"{base}/bind-agent/claim-credentials?token={token}", None
            )
            if not claim.get("ok"):
                return _fail(
                    agent_id, "claim-credentials",
                    _step_failure("claim-credentials", claim) + "." + _claim_hint(claim.get("status")),
                    f"HTTP {claim.get('status')} flow_status={status}",
                )
            creds = _parse_claim(claim.get("data") or {})
            if creds is None:
                present = sorted(k for k in _CLAIM_FIELDS if (claim.get("data") or {}).get(k))
                return _fail(
                    agent_id, "claim-credentials",
                    "NarraMessenger returned an incomplete agent credential." + _claim_hint(None),
                    f"fields present={present}",
                )

            # The homeserver comes from the link's host; gate it BEFORE
            # runtime-ready so a refused bind is never completed on the
            # NarraMessenger side, and nothing is persisted.
            homeserver = creds["homeserver_url"]
            if not await _is_allowed_target(homeserver):
                return _fail(
                    agent_id, "homeserver-gate",
                    "NarraMessenger named a Matrix homeserver that is "
                    "not a public host; the bind was not completed.",
                )

            # Direct Matrix bind. This is a bind-token URL (query param
            # ``?token=<bind_token>``), NOT a bearer-auth endpoint —
            # confusingly the guide has both ``/bind-agent/runtime-ready``
            # (this call, initial bind) and ``/api/agent-runtime/direct-ready``
            # (bearer-authenticated, used only when upgrading FROM Gateway
            # BACK to Direct after an initial bind). Do not mix them.
            # ``platform`` tags the agent's origin on the NarraMessenger side.
            # It rides on runtime-ready (not report-profile) because this is
            # the one call EVERY bind makes — report-profile is skipped
            # whenever the session is already at the credential step — and
            # because runtime-ready is the step that means "this agent is
            # usable", which is what their label describes.
            ready = await _post_json(
                session,
                f"{base}/bind-agent/runtime-ready?token={token}",
                {"platform": PLATFORM_TAG},
            )
            if not ready.get("ok"):
                # The claim is spent by now; a transient failure is recovered by
                # re-pasting the same link inside the claim's retry window.
                ready_status = ready.get("status")
                transient = ready_status is None or ready_status >= 500
                return _fail(
                    agent_id, "runtime-ready",
                    _step_failure("runtime-ready", ready)
                    + ("." + _claim_hint(None) if transient else ""),
                    f"HTTP {ready.get('status')}",
                )
            rdata = ready.get("data") or {}
    except UnsafeUrlError as e:
        return _fail(
            agent_id, "connect-gate",
            "The bind link does not point at a public NarraMessenger "
            "host. Re-copy the bind link from My Agents → Bind Agents.",
            str(e).replace(token, "<token>")[:200],
        )
    except (aiohttp.ClientError, asyncio.TimeoutError) as e:
        # Only the class name: aiohttp's messages embed the request URL, which
        # carries the bind token (e.g. "Connection timeout to host <url>").
        return _fail(
            agent_id, "network",
            f"network error reaching NarraMessenger: {type(e).__name__}",
            type(e).__name__,
        )

    matrix_user_id = rdata.get("matrixUserId") or creds["matrix_user_id"]
    principal_id = rdata.get("principalId", "") or ""
    bind_room_id = rdata.get("roomId", "") or ""

    mgr = NarramessengerCredentialManager(db)
    await mgr.upsert(
        NarramessengerCredential(
            agent_id=agent_id,
            bearer_token=creds["secret_token"],
            backend_base_url=base,
            matrix_homeserver_url=homeserver,
            matrix_user_id=matrix_user_id,
            matrix_access_token=creds["matrix_access_token"],
            nexus_principal_id=principal_id,
            nexus_profile_id=rdata.get("profileId", "") or "",
            bind_room_id=bind_room_id,
            connection_mode="matrix",
            enabled=True,
        )
    )
    logger.info(
        f"[narramessenger:{agent_id}] bound via Direct Matrix "
        f"(matrix_user_id={matrix_user_id}, principal={principal_id}, "
        f"bind_room={bind_room_id})"
    )
    return {
        "success": True,
        "data": {
            "matrix_user_id": matrix_user_id,
            "principal_id": principal_id,
            "room_id": bind_room_id,
            "connection_mode": "matrix",
        },
    }


async def do_unbind(db, agent_id: str) -> dict[str, Any]:
    """Remove the agent's NarraMessenger credential (stops the trigger)."""
    mgr = NarramessengerCredentialManager(db)
    ok = await mgr.unbind(agent_id)
    return {"success": True, "unbound": ok}
