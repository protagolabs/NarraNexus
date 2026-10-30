"""
@file_name: url_artifact.py
@author: Bin Liang
@date: 2026-07-22
@description: Create / update URL-tab artifacts (application/x-url).

A URL tab is an ordinary pointer artifact whose entry file is a small JSON
doc (UrlArtifactDoc) at `tabs/<slug>/page.url.json` in the agent workspace.
This module owns the doc I/O + orchestration; registration itself goes through
the shared `registration.register_artifact` so URL tabs get heal / delete /
bundle / raw-serving for free.

Each URL tab lives in its OWN subdirectory so the raw route's artifact-root
isolation means one tab can never read another tab's json.

No DB column for the URL — the JSON doc is the source of truth (pointer model
preserved). Updating the embed decision = rewriting the doc.
"""

from __future__ import annotations

import asyncio
import json
import os
import secrets
from typing import Optional
from urllib.parse import urlparse

from loguru import logger

from narranexus.platform.artifact._artifact_impl import confinement, registration
from narranexus.platform.artifact._artifact_impl.embed_probe import probe_url
from narranexus.platform.artifact._artifact_impl.errors import (
    ArtifactContentGone,
    ArtifactError,
    ArtifactNotFound,
    ArtifactPathEscape,
)
from narranexus.platform.artifact._artifact_impl.page_text import fetch_page_text
from narranexus.platform.repository.artifact_repository import ArtifactRepository
from narranexus.platform.schema.artifact_schema import (
    URL_ARTIFACT_KIND,
    URL_TAB_CONTENT_FILENAME,
    Artifact,
    CreateArtifactToolResult,
    EmbedMode,
    EmbedVerdict,
    UrlArtifactDoc,
)
from narranexus.platform.settings import settings
from narranexus.platform.utils.fs import confined_io
from narranexus.platform.utils.url_safety import UnsafeUrlError, assert_public_http_url

_URL_TABS_DIR = "tabs"
_DOC_FILENAME = "page.url.json"
# Total wall-clock budget for the two outbound fetches (probe + page text) done
# while opening a URL tab. Bounds how long this MCP tool blocks — an
# outbound-HTTP deadline, NOT an agent_loop ceiling (铁律 #14 untouched).
_OPEN_FETCH_BUDGET_S = 20.0


def _our_scheme() -> str:
    """The scheme WE are served on, for the mixed-content check. Cloud is
    https; local dev is http. Derived from settings.public_base_url when set,
    else assumed http (local run.sh)."""
    base = (settings.public_base_url or "").strip().lower()
    if base.startswith("https://"):
        return "https"
    return "http"


def _doc_abs_path(agent_id: str, user_id: str, rel_entry: str) -> str:
    """Absolute path of a URL-tab doc. `rel_entry` is relative to the AGENT
    WORKSPACE root (the same base registration._resolve_entry joins against),
    NOT to base_working_path — the nested layout lives under the workspace."""
    workspace = registration.workspace_root(agent_id, user_id)
    return os.path.join(workspace, rel_entry)


def _read_doc(abs_path: str) -> UrlArtifactDoc:
    # A missing doc is a REAL state under the pointer model (the agent can
    # move/delete workspace files) — surface it as 410, not a bare
    # FileNotFoundError that the route would turn into a 500 + traceback.
    # Opened through the confined walk (every component O_NOFOLLOW below the
    # base): the caller proved ``abs_path`` under the owner's roots, but a
    # directory swapped for a link since then must not be read through.
    try:
        with confinement.open_confined_read(abs_path) as f:
            return UrlArtifactDoc.model_validate_json(f.read().decode("utf-8"))
    except FileNotFoundError as e:
        raise ArtifactContentGone("URL-tab doc is missing on disk") from e


_DEFAULT_PORTS = {"http": 80, "https": 443}


def strip_like_browser(url: str) -> str:
    """Drop leading and trailing C0 controls and spaces, as URL parsers do."""
    return url.strip("".join(chr(c) for c in range(0x21)))


def _origin_tuple(url: str) -> Optional[tuple[str, str, Optional[int]]]:
    """(scheme, host, effective_port) normalized the way a BROWSER compares
    origins: scheme + host lowercased, userinfo dropped, the default port for
    the scheme filled in, the host run through IDNA so fullwidth letters and
    dots collapse as they do in the browser. Returns None when there is no host
    to compare, or when urlparse and a browser could read the URL differently.

    This is the crux of the self-origin guard: a naive `netloc == netloc`
    string compare is bypassable (`AGENT.narra.nexus`, `host:443`,
    `u@host` all read as same-origin to the browser but differ as strings).

    The refused shapes are parser differentials, not style. A browser treats a
    backslash as a slash (`https://ours\\@evil.example` is OURS to it and
    evil.example to urlparse), drops tabs and newlines anywhere in a URL (urlparse
    does too, silently), and percent-decodes the host (`%61gent.narra.nexus`).
    `https:/ours` has no netloc for urlparse and a host for the browser. Backslash
    and spaces only matter before the path ends: `?q=a\\b` and `/a b` are ordinary.
    Surrounding whitespace is stripped first, as a browser does.
    """
    url = strip_like_browser(url)
    if any(ch in url for ch in "\t\r\n"):
        return None
    head = url.split("?", 1)[0].split("#", 1)[0]
    authority = head.split("/", 3)[2] if head.count("/") >= 2 else head
    if "\\" in head or any(ord(ch) <= 0x20 for ch in authority):
        return None
    try:
        p = urlparse(url)
        if "%" in p.netloc or not p.hostname:
            return None
        host = p.hostname.encode("idna").decode("ascii").lower()
        scheme = (p.scheme or "").lower()
        port = p.port or _DEFAULT_PORTS.get(scheme)
    except ValueError:  # malformed netloc or port; UnicodeError is a ValueError
        return None
    return (scheme, host, port)


def _reject_self_origin(url: str, *, extra_origins: tuple[str, ...] = ()) -> None:
    """Refuse a URL whose browser-origin equals our own app's.

    A same-origin URL tab would become a same-origin iframe; with the
    renderer's `allow-same-origin allow-scripts` sandbox that iframe could
    reach the parent app's DOM / localStorage token and drive the app's API as
    the user. The SSRF gate only blocks private addresses — our own public
    origin is public — so this is the guard that keeps the `allow-same-origin`
    grant safe (a URL tab is only ever meant to hold cross-origin third-party
    content).

    This judges the URL as written, which is all it can see: a third-party page
    can still redirect the iframe to our origin. What holds there is the
    `Content-Security-Policy: sandbox` response header on our public content
    routes, which makes the landed document opaque whatever the iframe grants.

    A URL that cannot be judged is refused, on every deployment: `_origin_tuple`
    returning None means a browser may see a different host than we do.

    Candidate origins: `settings.public_base_url` (set in cloud; the MCP path
    has only this) plus any `extra_origins` the caller derived from the request
    (the HTTP route passes the browser-visible origin, so the guard holds even
    if public_base_url is misconfigured). Empty candidates are skipped — local
    dev without a configured origin has no shared tenant and its localhost URLs
    are already rejected by the SSRF loopback rule.
    """
    target = _origin_tuple(url)
    if target is None:
        raise ArtifactError("refusing to open a URL tab with an ambiguous or malformed URL")
    for candidate in (settings.public_base_url, *extra_origins):
        candidate = (candidate or "").strip()
        if not candidate:
            continue
        ours = _origin_tuple(candidate)
        if ours is not None and ours == target:
            raise ArtifactError("refusing to open a URL tab pointing at this app's own origin")


def _make_tab_dir(tab_dir_abs: str) -> None:
    """Create ``tabs/<slug>`` as REAL directories, walking every component
    below the base with O_NOFOLLOW.

    This is the proof-and-create step for a new tab: nothing is written
    anywhere until the whole path is shown link-free, and the creation itself
    never recurses through a link (a tenant-planted ``tabs -> <victim dir>``
    used to receive the doc via ``os.makedirs``). A link on the path raises
    ArtifactPathEscape with nothing created beyond it.
    """
    base_real, rel = confinement.split_under_base(tab_dir_abs)
    try:
        confined_io.make_dirs(base_real, rel)
    except confined_io.ConfinedPathError as e:
        raise ArtifactPathEscape(str(e)) from e


def _write_doc(abs_path: str, doc: UrlArtifactDoc) -> None:
    # Atomic write: a truncate-in-place crash (e.g. during a set_embed_mode
    # toggle) would leave a half-written doc that _read_doc can't parse,
    # bricking the tab. Temp file + rename (both via the parent dir's fd) so a
    # reader always sees either the old or the new complete doc, and the write
    # can never be redirected through a link swapped in after the caller's
    # confinement check. The directory must already exist (_make_tab_dir
    # creates it for a new tab; set_embed_mode only rewrites an existing doc).
    confinement.write_confined_atomic(abs_path, doc.model_dump_json(indent=2).encode("utf-8"))


def _write_content(dir_abs: str, *, url: str, title: str, page_text: Optional[str]) -> None:
    """Write the agent-readable `content.md` next to the doc. Always written
    (even when extraction failed) so the state-block hint always points at a
    real file. The body is a snapshot taken at open time."""
    if page_text:
        body = page_text
    else:
        body = (
            "_The page text could not be captured automatically (the site may "
            "block server-side fetches, require login, or render entirely with "
            "JavaScript). Ask the user what they need from this page._"
        )
    content = f"# {title}\n\nSource: {url}\n\n---\n\n{body}\n"
    confinement.write_confined_atomic(
        os.path.join(dir_abs, URL_TAB_CONTENT_FILENAME), content.encode("utf-8")
    )


async def open_url(
    *,
    repo: ArtifactRepository,
    db,
    agent_id: str,
    user_id: str,
    session_id: Optional[str],
    url: str,
    title: Optional[str] = None,
    app_origin: Optional[str] = None,
) -> CreateArtifactToolResult:
    """Create a URL-tab artifact.

    Steps:
    1. Reject a URL whose browser-origin is our own app (self-origin guard,
       keeps the renderer's allow-same-origin sandbox safe) and hard-reject an
       internal URL (SSRF gate). Both raise ArtifactError — creation fails loud.
    2. Probe the URL server-side for its embed verdict (never crashes; a
       failed probe degrades to an optimistic iframe verdict).
    3. Write the UrlArtifactDoc into a dedicated `tabs/<slug>/` subdir.
    4. Register it through the shared pointer path.

    Args:
        app_origin: the browser-visible origin of the app, derived from the
            request by the HTTP route (the MCP path leaves it None and relies
            on settings.public_base_url). Used only to widen the self-origin
            guard's defense.

    Raises:
        ArtifactError: URL is our own origin, or not a safe public http(s) target.
    """
    url = strip_like_browser(url)
    _reject_self_origin(url, extra_origins=(app_origin,) if app_origin else ())
    try:
        await assert_public_http_url(url)
    except UnsafeUrlError as e:
        raise ArtifactError(f"refusing to open a non-public URL: {e}") from e

    # Probe (embed verdict) and page-text capture are independent outbound
    # fetches — run them CONCURRENTLY under one wall-clock budget so opening a
    # tab isn't the sum of both worst cases. Both degrade rather than raise;
    # on timeout we fall back to an optimistic iframe verdict + no text.
    verdict: EmbedVerdict
    page_text: Optional[str]
    try:
        async with asyncio.timeout(_OPEN_FETCH_BUDGET_S):
            verdict, page_text = await asyncio.gather(
                probe_url(url, our_scheme=_our_scheme()),
                fetch_page_text(url),
            )
    except TimeoutError:
        logger.info("open_url: outbound fetch budget exceeded for {}", url)
        verdict = EmbedVerdict(recommended="iframe", reason="probe-failed", probe_status="failed")
        page_text = None

    slug = secrets.token_hex(4)
    rel_entry = f"{_URL_TABS_DIR}/{slug}/{_DOC_FILENAME}"
    abs_entry = _doc_abs_path(agent_id, user_id, rel_entry)
    resolved_title = (title or url)[:200]
    # Prove the target dir link-free and create it BEFORE any write.
    _make_tab_dir(os.path.dirname(abs_entry))
    _write_doc(abs_entry, UrlArtifactDoc(url=url, title=resolved_title, embed=verdict))
    # Sibling file the agent can Read (surfaced in the artifact state block).
    _write_content(os.path.dirname(abs_entry), url=url, title=resolved_title, page_text=page_text)

    result = await registration.register_artifact(
        repo=repo,
        db=db,
        agent_id=agent_id,
        user_id=user_id,
        session_id=session_id,
        kind=URL_ARTIFACT_KIND,  # type: ignore[arg-type]
        entry_path=rel_entry,
        title=resolved_title,
        description=url,  # human-readable pointer target for listings
        target_artifact_id=None,
    )
    logger.debug("Opened URL artifact {} -> {}", result.artifact_id, url)
    return result


async def set_embed_mode(
    *,
    repo: ArtifactRepository,
    agent_id: str,
    artifact_id: str,
    mode: Optional[EmbedMode],
) -> Artifact:
    """Set (or clear, with mode=None) the user's manual embed override on a
    URL tab, by rewriting its JSON doc. Returns the artifact row.

    Raises:
        ArtifactNotFound: artifact missing / not this agent's / not a URL tab /
            its doc pointer resolves outside the owner's roots.
    """
    art = await repo.get_by_id(artifact_id)
    if art is None or art.agent_id != agent_id or art.kind != URL_ARTIFACT_KIND:
        raise ArtifactNotFound("URL artifact not found")

    # Read AND rewrite only a doc proven to sit under the owner's roots; a
    # folder swapped for a link would otherwise aim the atomic rewrite at
    # another tenant's tab (2026-10-28). Out-of-roots reads as not-found.
    try:
        abs_entry = confinement.confine_entry(
            art.file_path, art.agent_id, art.user_id,
            settings.base_working_path, team_id=art.team_id,
        )
        doc = _read_doc(abs_entry)
    except ArtifactPathEscape:
        raise ArtifactNotFound("URL artifact not found") from None
    # The doc is a workspace file, so an agent can have edited its url since
    # open_url checked it. This only keeps the override API from re-blessing such
    # a doc: the browser reads the doc through the raw route, not through here,
    # so UrlRenderer repeats the check before it renders anything.
    _reject_self_origin(doc.url)
    if doc.embed is None:
        # No probe verdict on disk (shouldn't happen for a real URL tab, but be
        # defensive): synthesize an iframe default to hang the override on.
        doc.embed = EmbedVerdict(recommended="iframe", reason="no-blocking-headers")
    doc.embed.user_override = mode
    try:
        _write_doc(abs_entry, doc)
    except ArtifactPathEscape:
        raise ArtifactNotFound("URL artifact not found") from None

    # Bump updated_at so the frontend's refetch sees a change. update_title with
    # the unchanged title is a deliberate no-op-content / touch-timestamp write
    # (the repo has no bare `touch`); the override itself lives in the doc, not
    # the DB row.
    await repo.update_title(artifact_id, art.title)
    refreshed = await repo.get_by_id(artifact_id)
    assert refreshed is not None
    return refreshed
