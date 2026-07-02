"""
@file_name: matrix_trigger.py
@date: 2026-07-02
@description: NarraMessenger MatrixTrigger — replaces the polling long-poll
              on the message plane with a real Matrix client (matrix-nio)
              talking to matrix.netmind.chat directly.

Scope of THIS FILE (Phase 1, Commits 3 + 4b):
  ✓  Class + ChannelTriggerBase wiring, all abstract methods present
  ✓  ``connect()`` implements the sync loop with the strict cursor-save
     ordering (see :meth:`connect` docstring)
  ✓  ``parse_event()`` handles ``m.room.message`` (text)
  ✓  ``is_echo()`` filters our own agent's messages back out of the
     sync stream
  ✓  Auto-persist ``device_id`` on first sync (matrix-nio populates
     ``client.device_id`` after the server ack)
  ✓  (4b) Room member count + display name caches populated by
     ``m.room.member`` state events flowing through the sync loop
  ✓  (4b) DM / group_mention / group_silent classification, with
     mention detection covering MSC3952 intentional mentions, raw
     MXID inline, and ``@displayname`` inline
  ✓  (4b) Silent-batch buffer + debounce (5s idle OR 20 msgs) → hands
     off to :meth:`ChannelTriggerBase._build_and_run_agent_silent_batch`
     from Commit 4a; drained on reconnect burst AND on ``stop()``
  ✓  (4b) Reply sending via ``client.room_send`` with M_LIMIT_EXCEEDED
     retry-after honoring and permanent-auth-failure short-circuit;
     transient failures logged to ``EVENT_TRANSPORT_SEND_FAILED`` audit
  ✓  (4b) ``extract_output()`` reads
     ``send_message_to_user_directly`` tool call args — no more
     accidental agent-thinking spill into the room
  ✓  (4b) Silent-not-reply fix: agent that skips the reply tool sends
     nothing

Deferred to Phase 3:
  ✗  Multimodal: ``m.image`` / ``m.file`` / ``m.audio`` / ``m.video``
  ✗  Attachment download via authenticated /media/v1/download

Deferred to Phase 4:
  ✗  Progressive update via ``m.replace``
  ✗  Typing indicator via ``PUT /rooms/{room}/typing/{user}``
  ✗  ``NarramessengerContextBuilder`` adapting to matrix event shape
     (currently shared with the polling trigger — good enough for
     text-only Phase 1)

Design ref: [[Work/Narranexus/2026-07-02 NarraMessenger Matrix Adapter spec]]
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any, AsyncIterator, List, Literal, Optional

import aiohttp
from loguru import logger

# matrix-nio is added as a hard dep in ``pyproject.toml`` (2026-07-02).
# Non-``[e2e]`` variant on purpose — matrix.netmind.chat rooms are
# plaintext-by-default (verified 2026-07-02), so we skip libolm and its
# native-C dependency.
from nio import (
    AsyncClient,
    AsyncClientConfig,
    LocalProtocolError,
    RoomMemberEvent,
    RoomMessageText,
    RoomSendError,
    RoomSendResponse,
)

from xyz_agent_context.channel.channel_audit_events import (
    EVENT_TRANSPORT_SEND_FAILED,
)
from xyz_agent_context.channel.channel_context_builder_base import (
    ChannelContextBuilderBase,
    ChannelHistoryConfig,
)
from xyz_agent_context.channel.channel_trigger_base import ChannelTriggerBase
from xyz_agent_context.schema.hook_schema import WorkingSource
from xyz_agent_context.schema.parsed_message import ChatType, ParsedMessage

from ._narramessenger_credential_manager import (
    NarramessengerCredential,
    NarramessengerCredentialManager,
)
from .narramessenger_context_builder import NarramessengerContextBuilder


_ClassifyTarget = Literal["dm", "group_mention", "group_silent"]


@dataclass(frozen=True)
class _AuthorizeVerdict:
    """Result of the Narra ``authorize-event`` gate for one Matrix event.

    - ``allow=True`` → proceed with normal handling (silent OR full).
    - ``allow=False`` + ``notice_send=True`` → send ``notice_text`` back
      as an ``m.notice`` (server-suggested denial message) then stop.
    - ``allow=False`` + ``notice_send=False`` → drop silently. This is
      the fail-closed default for HTTP errors, timeouts, invalid JSON,
      and any ``allow != true`` response without a notice payload.
    """
    allow: bool
    notice_send: bool = False
    notice_text: Optional[str] = None


class MatrixTrigger(ChannelTriggerBase):
    """NarraMessenger channel trigger — Matrix client transport.

    Runs in the same process as the legacy ``NarramessengerTrigger``
    (polling). Both subclass :class:`ChannelTriggerBase`; each filters
    the credentials table to its own ``connection_mode`` value so they
    never fight over the same agent.

    The name ``narramessenger_matrix`` is deliberately distinct from
    ``narramessenger`` so the base's dedup store, audit log, and future
    per-channel stats can differentiate the transports — dedup keys use
    Matrix ``event_id`` here vs NarraMessenger ``invocation_id`` on the
    legacy trigger, and the two ID spaces must not collide in one bucket.
    """

    # ── ChannelTriggerBase contract ───────────────────────────────────────
    # Distinct channel_name so dedup/audit tables partition cleanly, but
    # working_source stays NARRAMESSENGER — everything downstream of
    # parse_event (context builder, reply routing, agent module) treats
    # matrix and polling as the same product surface.
    channel_name = "narramessenger_matrix"
    brand_display = "NarraMessenger"
    working_source = WorkingSource.NARRAMESSENGER

    # ── Worker pool ──────────────────────────────────────────────────────
    # Mirrors NarramessengerTrigger's tuning. Kept identical on purpose:
    # migration should not change concurrency characteristics.
    MIN_WORKERS = 3
    WORKERS_PER_SUBSCRIBER = 2
    MAX_WORKERS = 50
    PROCESS_MESSAGE_TIMEOUT_SECONDS = 1800
    CLEANUP_INTERVAL_SECONDS = 24 * 3600
    HEARTBEAT_INTERVAL_SECONDS = 600
    DEDUP_RETENTION_DAYS = 7
    AUDIT_RETENTION_DAYS = 30

    DEDUP_TTL_SECONDS = 600
    HISTORY_BUFFER_MS = 5 * 60 * 1000

    DEBOUNCE_WINDOW_MS = 0

    # ── Matrix sync tuning ───────────────────────────────────────────────
    # Server long-poll timeout. Synapse holds up to 30s idle; new events
    # flush earlier. Full events, not lazy_load_members — the room member
    # list is needed for mention-vs-DM routing (Commit 4).
    SYNC_TIMEOUT_MS = 30000
    # Initial sync (empty since token) — full state might be large on
    # bounded but active accounts; a short server timeout prevents the
    # first connect from hanging on a quiet server.
    FIRST_SYNC_TIMEOUT_MS = 5000

    # ── Silent-batch buffering ───────────────────────────────────────────
    # Group non-@ messages accumulate in a per-(agent, room) buffer and
    # flush on N seconds idle OR N messages, whichever comes first. Values
    # kept modest so the memory-write side (chat_history, observations,
    # entity descriptions) is never more than N seconds behind reality.
    SILENT_DEBOUNCE_SECONDS = 5.0
    SILENT_FLUSH_BURST_SIZE = 20

    # ── Reply sender retry policy ────────────────────────────────────────
    # room_send failures come in three shapes: (a) M_LIMIT_EXCEEDED
    # (transient, server tells us retry_after_ms), (b) auth failure
    # (M_UNKNOWN_TOKEN etc — permanent, the base's watcher already flips
    # enabled=False on next sync tick when it sees the same code), (c)
    # network / server 5xx (retriable with exponential backoff). We cap
    # at 3 attempts total so a legitimately down homeserver doesn't hold
    # a worker for minutes; the missed reply lands in audit and the owner
    # can see it after the fact.
    SEND_MAX_ATTEMPTS = 3
    SEND_INITIAL_BACKOFF_MS = 500

    # ── Narra authorize-event gate ───────────────────────────────────────
    # Every Matrix event must clear ``POST /api/agent-runtime/matrix/
    # authorize-event`` before we read history, write memory, invoke
    # tools, call the model, or send a Matrix reply. Fail-closed on
    # ANY non-2xx / invalid-JSON / timeout / ``allow != true`` — those
    # all count as "do not process". 401 during a pending bind is
    # expected fail-closed behaviour, NOT a signal to disable the
    # credential (the base's is_permanent_auth_failure only fires on
    # Matrix auth codes, not Narra ones).
    AUTHORIZE_EVENT_TIMEOUT_SECONDS = 10.0
    AUTHORIZE_EVENT_PATH = "/api/agent-runtime/matrix/authorize-event"

    def __init__(self, max_workers: int = 3):
        super().__init__(
            base_workers=max_workers,
            # Matrix /sync already carries recent room timeline per response,
            # so we don't need the base's separate per-room history fetch;
            # the context builder reads history off ParsedMessage.raw the
            # same way NarramessengerTrigger does.
            history_config=ChannelHistoryConfig(
                load_conversation_history=False,
                history_limit=20,
                history_max_chars=20000,
            ),
        )
        # Kept per-credential so stop() can close each connection cleanly.
        self._clients: dict[str, AsyncClient] = {}

        # ── Caches populated by sync-response state / timeline walks ─────
        # room_id -> current joined member count. Used to classify DM
        # (==2) vs group (>2). Populated lazily on first sight; kept
        # accurate by watching ``m.room.member`` events for join/leave.
        self._room_member_count: dict[str, int] = {}
        # (room_id, mxid) -> current displayname. Populated the same way,
        # since displayname lives on the ``m.room.member`` event content.
        # Used for @-mention detection AND per-message attribution when
        # writing chat_history from silent batches.
        self._display_name_cache: dict[tuple[str, str], str] = {}

        # ── Silent-batch buffer ──────────────────────────────────────────
        # Keyed by (agent_id, room_id) so a single-process trigger managing
        # multiple agents keeps their group-silent streams isolated. Each
        # buffer entry is a ParsedMessage kept in arrival order.
        self._silent_buffer: dict[tuple[str, str], list[ParsedMessage]] = {}
        # Corresponding credential per buffer key — we need it at flush
        # time (which fires on a timer, not in the enqueuing coroutine).
        self._silent_creds: dict[tuple[str, str], NarramessengerCredential] = {}
        # Pending debounce timer per buffer key. Cancelled and replaced on
        # every new enqueue so 5s idle == "no new msg in 5s".
        self._silent_flush_tasks: dict[tuple[str, str], asyncio.Task] = {}
        # Per-key lock — flush is async and buffer mutation must not race
        # a concurrent enqueue that arrives while we're draining.
        self._silent_locks: dict[tuple[str, str], asyncio.Lock] = {}

    # ────────────────────────────────────────────────────────────────────
    # Lifecycle
    # ────────────────────────────────────────────────────────────────────

    async def start(self, db) -> None:
        await super().start(db)
        logger.info(
            f"MatrixTrigger started: {len(self._workers)} workers, "
            f"watching channel_narramessenger_credentials "
            f"(connection_mode='matrix') for active rows"
        )

    async def stop(self) -> None:
        # Drain any pending silent buffers BEFORE closing clients — the
        # flush path uses the runtime client (which is in-process today
        # and doesn't need the matrix client), but we still want the
        # memory writes to land before we tear down. If drain takes too
        # long the outer stop timeout will surface it.
        try:
            await asyncio.wait_for(
                self._drain_all_silent_buffers(), timeout=10.0
            )
        except asyncio.TimeoutError:
            logger.warning(
                "MatrixTrigger.stop: silent buffer drain timed out; "
                "some group-silent messages may not be persisted"
            )
        # Cancel any leftover debounce timers regardless of drain state.
        for key, task in list(self._silent_flush_tasks.items()):
            if not task.done():
                task.cancel()
        self._silent_flush_tasks.clear()

        for key, client in list(self._clients.items()):
            try:
                await asyncio.wait_for(client.close(), timeout=3.0)
            except (asyncio.TimeoutError, Exception) as e:  # noqa: BLE001
                logger.warning(
                    f"[matrix:{key}] client close during stop: {e}"
                )
        self._clients.clear()
        await super().stop()

    # ────────────────────────────────────────────────────────────────────
    # Abstract-method implementations
    # ────────────────────────────────────────────────────────────────────

    async def load_active_credentials(
        self,
    ) -> list[NarramessengerCredential]:
        """Return only credentials on the matrix transport.

        Filtering here is what keeps this trigger and the legacy
        NarramessengerTrigger from cross-driving the same agent. Uses
        the composite ``(connection_mode, enabled)`` index added by
        the 2026-07-02 schema migration.
        """
        if not self._db:
            return []
        mgr = NarramessengerCredentialManager(self._db)
        return await mgr.list_active_by_mode("matrix")

    def _subscriber_key(  # type: ignore[override]
        self, credential: NarramessengerCredential
    ) -> str:
        # Distinct key namespace from the polling subscriber so both
        # transports can key by agent_id without collision inside the
        # base's ``_subscriber_tasks`` map.
        return f"matrix:{credential.agent_id}"

    def is_permanent_auth_failure(  # type: ignore[override]
        self, exc: BaseException
    ) -> bool:
        """M_UNKNOWN_TOKEN and friends — a re-bind is required.

        matrix-nio surfaces ``M_UNKNOWN_TOKEN`` and other terminal auth
        errors in the response object; when we call ``.transport_response``
        directly (rare) it may raise ``LocalProtocolError``. The base
        treats these as "disable the credential, stop reconnecting".
        """
        text = str(exc)
        return (
            "M_UNKNOWN_TOKEN" in text
            or "M_MISSING_TOKEN" in text
            or "M_FORBIDDEN" in text
            or isinstance(exc, LocalProtocolError)
        )

    async def disable_credential(  # type: ignore[override]
        self, credential: NarramessengerCredential
    ) -> None:
        if not self._db:
            return
        mgr = NarramessengerCredentialManager(self._db)
        await mgr.set_enabled(credential.agent_id, False)

    def create_context_builder(  # type: ignore[override]
        self,
        message: ParsedMessage,
        credential: NarramessengerCredential,
        agent_id: str,
    ) -> ChannelContextBuilderBase:
        # Reuse the same builder as the polling trigger. Commit 4 will
        # decide whether it needs a matrix-specific variant or whether
        # the differences can be papered over via ParsedMessage.raw
        # fields.
        return NarramessengerContextBuilder(
            message=message,
            credential=credential,
            agent_id=agent_id,
        )

    async def is_echo(  # type: ignore[override]
        self,
        message: ParsedMessage,
        credential: NarramessengerCredential,
    ) -> bool:
        """Drop events sent by our own agent identity.

        Matrix's /sync returns every event in every room we're in,
        including the ones our own client just sent. Without this
        filter the trigger would immediately re-process every reply
        it just sent, creating a feedback loop.

        matrix-nio also filters our own device's events out of the
        default sync response; this check is belt-and-braces in case
        the server's device model produces an ordering edge case.
        """
        if not credential.matrix_user_id:
            return False
        return message.sender_id == credential.matrix_user_id

    async def resolve_sender_name(  # type: ignore[override]
        self, sender_id: str, credential: NarramessengerCredential
    ) -> str:
        """Room-state-backed display name lookup with MXID fallback.

        Walks the per-room display name cache first (populated by member
        state events flowing through the sync loop). If the cache misses
        we return the MXID unchanged — better than blocking a worker on
        an ad-hoc ``room_get_state_event`` call, and the next member
        event in the sync stream will backfill the cache anyway.
        """
        # No room context in this signature; the cache is per-(room, mxid),
        # so scan for any room where we've seen this mxid. In practice each
        # sender has one canonical display name across all rooms of a
        # homeserver anyway.
        for (_room, mxid), name in self._display_name_cache.items():
            if mxid == sender_id and name:
                return name
        return sender_id

    # ────────────────────────────────────────────────────────────────────
    # Room-state consumption (populates member count + display name caches)
    # ────────────────────────────────────────────────────────────────────

    def _apply_member_event(self, room_id: str, event: RoomMemberEvent) -> None:
        """Update caches from a single ``m.room.member`` state event.

        Called from the sync loop for both timeline events (live changes)
        and state events (initial snapshot on first sync). Idempotent —
        replaying the same event twice sets the cache to the same value.

        Membership transitions:
          - ``join``  → +1 member if we hadn't counted them; refresh name
          - ``leave`` → -1 member (never negative); drop the name entry
          - ``ban`` / ``knock`` / ``invite`` → member-count changes only
            when transitioning INTO join/OUT of join, so we skip these
            for the count and just handle name refresh.
        """
        mxid = event.state_key or event.sender or ""
        membership = event.membership or ""
        prev_membership = event.prev_membership or ""
        content = event.content if isinstance(event.content, dict) else {}
        name = content.get("displayname") or ""

        # Display name: update on any join / knock-with-name, drop on leave.
        if membership == "join" and name:
            self._display_name_cache[(room_id, mxid)] = name
        elif membership == "leave":
            self._display_name_cache.pop((room_id, mxid), None)

        # Member count deltas — only transitions INTO/OUT of "join" matter.
        was_joined = prev_membership == "join"
        is_joined = membership == "join"
        if not was_joined and is_joined:
            self._room_member_count[room_id] = (
                self._room_member_count.get(room_id, 0) + 1
            )
        elif was_joined and not is_joined:
            self._room_member_count[room_id] = max(
                0, self._room_member_count.get(room_id, 1) - 1
            )

    async def _get_member_count(
        self, client: AsyncClient, room_id: str
    ) -> int:
        """Return current joined-member count for ``room_id``.

        Cache-first. On miss, one authenticated GET to
        ``/_matrix/client/v3/rooms/{room}/joined_members`` populates the
        cache. Returns 0 on failure — callers must treat 0 as "unknown,
        default to group_silent" so we NEVER accidentally auto-reply to a
        room whose shape we can't verify.
        """
        cached = self._room_member_count.get(room_id)
        if cached is not None:
            return cached
        try:
            resp = await client.joined_members(room_id)
        except Exception as e:  # noqa: BLE001
            logger.warning(
                f"[matrix] joined_members({room_id}) raised: "
                f"{type(e).__name__}: {e}"
            )
            return 0
        # nio returns JoinedMembersResponse (success) or JoinedMembersError
        # (failure). Success has .members (list of RoomMember dataclasses).
        members = getattr(resp, "members", None)
        if not isinstance(members, list):
            logger.warning(
                f"[matrix] joined_members({room_id}) returned no members "
                f"(status={getattr(resp, 'status_code', 'unknown')})"
            )
            return 0
        count = len(members)
        self._room_member_count[room_id] = count
        # Backfill display names from the roster too so the first message
        # in a fresh room doesn't miss @-mention detection.
        for m in members:
            mxid = getattr(m, "user_id", None)
            name = getattr(m, "display_name", None)
            if mxid and name:
                self._display_name_cache[(room_id, mxid)] = name
        return count

    # ────────────────────────────────────────────────────────────────────
    # Classification (DM / group_mention / group_silent)
    # ────────────────────────────────────────────────────────────────────

    def _is_mentioning_us(
        self,
        message: ParsedMessage,
        credential: NarramessengerCredential,
    ) -> bool:
        """True if the message @-mentions our agent.

        Split out from ``_classify`` in Commit 5 so the Narra
        authorize-event gate can compute the ``mentioned`` payload flag
        BEFORE classification runs. (Authorize-event must fire before we
        do any of "read history / write memory / call tool / call model
        / send reply" — see [[matrix_trigger.py]] Commit 5 note.)

        Mention detection covers three surfaces different clients emit:

        1. MSC3952 intentional mentions — the modern protocol,
           ``m.mentions.user_ids`` on the raw event content.
        2. Raw MXID inline in body — some clients still do this
           (``hey @agent-abc:h can you...``).
        3. ``@displayname`` inline — most human-typed mentions
           (``hey @Agent Bot ...``); requires the display name cache
           to have been populated by prior member state events.
        """
        body = (message.content or "").strip()
        my_id = credential.matrix_user_id or ""

        # (1) Intentional Mentions on the raw event.
        raw = message.raw or {}
        nio_event = raw.get("_nio_event")
        if nio_event is not None:
            source = getattr(nio_event, "source", None)
            if isinstance(source, dict):
                mentions = (
                    source.get("content", {}).get("m.mentions", {})
                    if isinstance(source.get("content"), dict)
                    else {}
                )
                user_ids = mentions.get("user_ids") if isinstance(mentions, dict) else None
                if isinstance(user_ids, list) and my_id and my_id in user_ids:
                    return True

        # (2) Raw MXID in body.
        if my_id and my_id in body:
            return True

        # (3) ``@displayname`` in body — walk the cache for our own name.
        my_names = {
            name
            for (_room, mxid), name in self._display_name_cache.items()
            if mxid == my_id and name
        }
        for name in my_names:
            if f"@{name}" in body or (name and name in body.split()):
                return True

        return False

    async def _classify(
        self,
        client: AsyncClient,
        message: ParsedMessage,
        credential: NarramessengerCredential,
        *,
        mentioned: Optional[bool] = None,
    ) -> _ClassifyTarget:
        """Route a message to the right handler.

        - Member count 2 → DM (1:1 owner-agent room, or any 2-person room)
        - Member count >2 AND agent explicitly mentioned → group_mention
        - Member count >2 AND agent NOT mentioned → group_silent
        - Member count 0 (unknown) → group_silent (safe default: don't
          auto-reply to a room we can't verify the shape of; the
          silent-batch path still writes memory)

        Args:
            mentioned: If already computed by the caller (e.g. the
                authorize-event gate already needed it for its payload),
                reuse rather than re-scanning the body. If None, we
                compute via :meth:`_is_mentioning_us`.
        """
        count = await self._get_member_count(client, message.chat_id)
        if count == 2:
            return "dm"
        if mentioned is None:
            mentioned = self._is_mentioning_us(message, credential)
        return "group_mention" if mentioned else "group_silent"

    # ────────────────────────────────────────────────────────────────────
    # Silent-batch buffer + debounce
    # ────────────────────────────────────────────────────────────────────

    async def _enqueue_silent(
        self,
        credential: NarramessengerCredential,
        message: ParsedMessage,
    ) -> None:
        """Append to the per-(agent, room) silent buffer + (re)start debounce.

        Two exit triggers race: burst-cap (immediate flush when the
        buffer hits SILENT_FLUSH_BURST_SIZE) and idle (SILENT_DEBOUNCE_
        SECONDS after the LAST enqueue — every new enqueue cancels and
        replaces the pending flush timer).
        """
        key = (credential.agent_id, message.chat_id)
        lock = self._silent_locks.setdefault(key, asyncio.Lock())
        async with lock:
            buf = self._silent_buffer.setdefault(key, [])
            buf.append(message)
            self._silent_creds[key] = credential
            size = len(buf)
        # Cancel any pending debounce; a new one starts below, or the
        # burst-cap path flushes immediately.
        existing = self._silent_flush_tasks.pop(key, None)
        if existing is not None and not existing.done():
            existing.cancel()
        if size >= self.SILENT_FLUSH_BURST_SIZE:
            await self._flush_silent(key)
        else:
            self._silent_flush_tasks[key] = asyncio.create_task(
                self._debounce_flush(key)
            )

    async def _debounce_flush(self, key: tuple[str, str]) -> None:
        """Sleep-then-flush task. Cancelled by every new enqueue."""
        try:
            await asyncio.sleep(self.SILENT_DEBOUNCE_SECONDS)
        except asyncio.CancelledError:
            return
        await self._flush_silent(key)

    async def _flush_silent(self, key: tuple[str, str]) -> None:
        """Drain the buffer for ``key`` and hand it off to the 4a silent-
        batch runtime call. Errors are swallowed — a dropped batch is
        recoverable via reconnect + since_token replay, but a raised
        exception here would break the debounce timer for the room."""
        lock = self._silent_locks.setdefault(key, asyncio.Lock())
        async with lock:
            msgs = self._silent_buffer.pop(key, [])
            cred = self._silent_creds.pop(key, None)
        self._silent_flush_tasks.pop(key, None)
        if not msgs or cred is None:
            return
        # Pre-resolve display names once for the whole batch.
        sender_names: dict[str, str] = {}
        for m in msgs:
            sid = m.sender_id or ""
            if not sid or sid in sender_names:
                continue
            # (room, mxid) lookup; fall back to MXID.
            name = self._display_name_cache.get((m.chat_id, sid))
            sender_names[sid] = name or sid
        try:
            await self._build_and_run_agent_silent_batch(
                credential=cred,
                messages=msgs,
                sender_name_by_id=sender_names,
            )
        except Exception as e:  # noqa: BLE001
            logger.exception(
                f"[matrix:{cred.agent_id}] silent flush raised "
                f"(batch of {len(msgs)}): {e}"
            )

    async def _drain_all_silent_buffers(self) -> None:
        """Flush every pending silent buffer. Called from stop() so we
        don't lose in-flight memory writes on shutdown, and after
        reconnect burst so backfill lands immediately."""
        keys = list(self._silent_buffer.keys())
        for key in keys:
            task = self._silent_flush_tasks.pop(key, None)
            if task is not None and not task.done():
                task.cancel()
            await self._flush_silent(key)

    # ────────────────────────────────────────────────────────────────────
    # Narra authorize-event gate + m.notice sender
    # ────────────────────────────────────────────────────────────────────

    def _room_member_mxids(self, room_id: str) -> list[str]:
        """Return the joined-member MXIDs we've seen for ``room_id``.

        Narra explicitly says it does NOT trust caller-supplied
        membership as authority (see setup-guide.md `Direct Group
        Context` section) — the server re-verifies. So this is a hint,
        not a source of truth. Empty list is fine; the server will
        still authorize based on its own view of the room.
        """
        return [
            mxid
            for (rid, mxid) in self._display_name_cache.keys()
            if rid == room_id
        ]

    async def _authorize_event(
        self,
        credential: NarramessengerCredential,
        message: ParsedMessage,
        *,
        mentioned: bool,
    ) -> _AuthorizeVerdict:
        """Call Narra's ``authorize-event`` gate for one Matrix event.

        Contract (from NarraMessenger setup guide, ``Matrix Authorize
        Event`` section):

            POST {backend}/api/agent-runtime/matrix/authorize-event
            Authorization: Bearer <Narra agent secret token>
            {
                roomId, senderMatrixUserId,
                memberMatrixUserIds, mentioned
            }

        Response shape:
            {"allow": bool, "notice": {"send": bool, "text": str}}

        Fail-closed rules (all → allow=False):
        - non-2xx status (401 during pending bind is EXPECTED here —
          not a permanent auth failure, just wait for owner to
          complete runtime-ready)
        - transport error / timeout
        - non-JSON body
        - allow field missing or not exactly ``True``

        Narra intentionally fails closed on unknown roomId, suspended
        bind, guide contract mismatch. We honour that unconditionally.
        """
        base_url = getattr(credential, "backend_base_url", "") or ""
        bearer = getattr(credential, "bearer_token", "") or ""
        if not base_url or not bearer:
            # Missing bind bearer means the credential row is not fully
            # provisioned yet; fail-closed with no notice.
            logger.warning(
                f"[matrix:{credential.agent_id}] authorize-event skipped: "
                f"missing backend_base_url or bearer_token; treating as deny"
            )
            return _AuthorizeVerdict(allow=False)

        url = f"{base_url.rstrip('/')}{self.AUTHORIZE_EVENT_PATH}"
        members = self._room_member_mxids(message.chat_id)
        # Guarantee at least sender + our agent are present, even if
        # cache is empty (fresh room, first event).
        if credential.matrix_user_id and credential.matrix_user_id not in members:
            members = members + [credential.matrix_user_id]
        if message.sender_id and message.sender_id not in members:
            members = members + [message.sender_id]

        payload = {
            "roomId": message.chat_id,
            "senderMatrixUserId": message.sender_id,
            "memberMatrixUserIds": members,
            "mentioned": bool(mentioned),
        }
        headers = {
            "Authorization": f"Bearer {bearer}",
            "Content-Type": "application/json",
        }
        timeout = aiohttp.ClientTimeout(
            total=self.AUTHORIZE_EVENT_TIMEOUT_SECONDS
        )

        try:
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.post(url, json=payload, headers=headers) as resp:
                    status = resp.status
                    if status < 200 or status >= 300:
                        # 401 during pending bind is expected; log at
                        # info so it doesn't spam ERROR during the
                        # bind-in-progress window.
                        level = "info" if status == 401 else "warning"
                        getattr(logger, level)(
                            f"[matrix:{credential.agent_id}] authorize-event "
                            f"non-2xx status={status} room={message.chat_id} "
                            f"→ fail-closed deny"
                        )
                        return _AuthorizeVerdict(allow=False)
                    try:
                        data = await resp.json()
                    except (aiohttp.ContentTypeError, ValueError) as e:
                        logger.warning(
                            f"[matrix:{credential.agent_id}] authorize-event "
                            f"invalid JSON: {type(e).__name__}: {e} "
                            f"→ fail-closed deny"
                        )
                        return _AuthorizeVerdict(allow=False)
        except (asyncio.TimeoutError, aiohttp.ClientError) as e:
            logger.warning(
                f"[matrix:{credential.agent_id}] authorize-event transport "
                f"error: {type(e).__name__}: {e} → fail-closed deny"
            )
            return _AuthorizeVerdict(allow=False)

        allow = data.get("allow") is True
        notice = data.get("notice") if isinstance(data, dict) else None
        notice_send = False
        notice_text: Optional[str] = None
        if isinstance(notice, dict):
            notice_send = notice.get("send") is True
            raw_text = notice.get("text")
            if isinstance(raw_text, str) and raw_text.strip():
                notice_text = raw_text
        return _AuthorizeVerdict(
            allow=allow,
            notice_send=notice_send if not allow else False,
            notice_text=notice_text if not allow else None,
        )

    async def _send_matrix_notice(
        self,
        credential: NarramessengerCredential,
        room_id: str,
        text: str,
    ) -> None:
        """Send Narra's server-suggested denial notice as ``m.notice``.

        Per guide: this is the ONLY allowed side effect for a denied
        event. Do NOT read history, write memory, invoke tools, call
        the model, or send anything other than exactly this text as
        ``m.notice``. If the send itself fails, we log — Narra told
        us to deliver this text and we tried; audit it in the standard
        transport_send_failed path so ops can see it, but do NOT
        retry the underlying event handling.
        """
        key = self._subscriber_key(credential)
        client = self._clients.get(key)
        if client is None:
            logger.warning(
                f"[matrix:{credential.agent_id}] cannot send authorize "
                f"notice: no active client for room={room_id}"
            )
            return
        try:
            resp = await client.room_send(
                room_id=room_id,
                message_type="m.room.message",
                content={"msgtype": "m.notice", "body": text},
            )
            if not isinstance(resp, RoomSendResponse):
                code = str(getattr(resp, "status_code", "unknown") or "unknown")
                logger.warning(
                    f"[matrix:{credential.agent_id}] authorize-event "
                    f"notice send failed: {code} (room={room_id})"
                )
        except Exception as e:  # noqa: BLE001
            logger.warning(
                f"[matrix:{credential.agent_id}] authorize-event notice "
                f"send raised: {type(e).__name__}: {e} (room={room_id})"
            )

    # ────────────────────────────────────────────────────────────────────
    # Reply sender + failure notification
    # ────────────────────────────────────────────────────────────────────

    async def _send_matrix_reply(
        self,
        credential: NarramessengerCredential,
        room_id: str,
        content: str,
    ) -> bool:
        """Send ``content`` back into ``room_id`` as ``m.room.message``.

        Retry policy:
          - M_LIMIT_EXCEEDED → obey ``retry_after_ms``, then retry.
          - M_UNKNOWN_TOKEN / M_MISSING_TOKEN / M_FORBIDDEN → audit +
            give up (the base's watcher will disable the credential on
            the next sync tick when it sees the same code).
          - Anything else (network, 5xx, unknown status) → exponential
            backoff, cap SEND_MAX_ATTEMPTS.

        Returns True iff the reply was accepted by the homeserver.
        A False return does NOT re-raise — the sync loop must not stall
        on a single failed reply; the audit event carries the diagnostic.
        """
        key = self._subscriber_key(credential)
        client = self._clients.get(key)
        if client is None:
            logger.warning(
                f"[matrix:{credential.agent_id}] cannot send reply: "
                f"no active client (sync loop torn down?)"
            )
            await self._audit(
                EVENT_TRANSPORT_SEND_FAILED,
                agent_id=credential.agent_id,
                app_id=getattr(credential, "app_id", ""),
                chat_id=room_id,
                details={
                    "error_code": "no_active_client",
                    "attempts": 0,
                    "body_preview": (content or "")[:120],
                },
            )
            return False

        backoff_ms = self.SEND_INITIAL_BACKOFF_MS
        last_code: str = ""
        last_error: str = ""

        for attempt in range(1, self.SEND_MAX_ATTEMPTS + 1):
            try:
                resp = await client.room_send(
                    room_id=room_id,
                    message_type="m.room.message",
                    content={"msgtype": "m.text", "body": content},
                )
            except Exception as e:  # noqa: BLE001
                last_code = "transport_exception"
                last_error = f"{type(e).__name__}: {e}"
                logger.warning(
                    f"[matrix:{credential.agent_id}] room_send raised on "
                    f"attempt {attempt}/{self.SEND_MAX_ATTEMPTS}: {last_error}"
                )
                if attempt < self.SEND_MAX_ATTEMPTS:
                    await asyncio.sleep(backoff_ms / 1000)
                    backoff_ms *= 2
                continue

            if isinstance(resp, RoomSendResponse):
                return True

            if isinstance(resp, RoomSendError):
                last_code = str(getattr(resp, "status_code", "") or "unknown")
                last_error = str(
                    getattr(resp, "message", "") or last_code
                )
                if last_code == "M_LIMIT_EXCEEDED":
                    retry_ms = int(getattr(resp, "retry_after_ms", 1000) or 1000)
                    logger.info(
                        f"[matrix:{credential.agent_id}] rate-limited; "
                        f"sleeping {retry_ms}ms before retry "
                        f"(attempt {attempt}/{self.SEND_MAX_ATTEMPTS})"
                    )
                    await asyncio.sleep(retry_ms / 1000)
                    continue
                if last_code in (
                    "M_UNKNOWN_TOKEN",
                    "M_MISSING_TOKEN",
                    "M_FORBIDDEN",
                ):
                    logger.error(
                        f"[matrix:{credential.agent_id}] permanent send "
                        f"failure {last_code}; leaving credential for "
                        f"the sync loop to disable"
                    )
                    break
                logger.warning(
                    f"[matrix:{credential.agent_id}] room_send returned "
                    f"{last_code} on attempt {attempt}/"
                    f"{self.SEND_MAX_ATTEMPTS}: {last_error}"
                )
                if attempt < self.SEND_MAX_ATTEMPTS:
                    await asyncio.sleep(backoff_ms / 1000)
                    backoff_ms *= 2
                continue

            # Neither success nor structured error — nio returned something
            # unexpected. Log and treat as transient.
            last_code = "unknown_response"
            last_error = f"{type(resp).__name__}"
            logger.warning(
                f"[matrix:{credential.agent_id}] room_send returned "
                f"unexpected {last_error} on attempt {attempt}"
            )
            if attempt < self.SEND_MAX_ATTEMPTS:
                await asyncio.sleep(backoff_ms / 1000)
                backoff_ms *= 2

        await self._audit(
            EVENT_TRANSPORT_SEND_FAILED,
            agent_id=credential.agent_id,
            app_id=getattr(credential, "app_id", ""),
            chat_id=room_id,
            details={
                "error_code": last_code,
                "error_message": last_error[:400],
                "attempts": self.SEND_MAX_ATTEMPTS,
                "body_preview": (content or "")[:120],
            },
        )
        return False

    # ────────────────────────────────────────────────────────────────────
    # Overrides: classify → route silent vs full-agent; then send reply
    # ────────────────────────────────────────────────────────────────────

    async def _process_message(  # type: ignore[override]
        self,
        credential: NarramessengerCredential,
        message: ParsedMessage,
    ) -> None:
        """Gate → classify → route.

        Pipeline (Commit 5):

        1. Drop if no active client (sync torn down mid-flight).
        2. Drop if echo (our own agent's message).
        3. **Narra authorize-event gate**. Per NarraMessenger's Direct
           Matrix contract, MUST call ``POST /api/agent-runtime/matrix/
           authorize-event`` BEFORE reading history, writing memory,
           invoking tools, calling the model, or sending a reply. Applies
           to both silent AND full paths because silent still writes
           memory. On deny + notice, forward the notice as ``m.notice``
           then stop; on deny + no notice, drop silently.
        4. Classify DM / group_mention / group_silent; route accordingly.

        Echo filtering happens IN the classifier's client lookup /
        super(); duplicating it here would risk drift. The base already
        also handles unbound-credential and empty-content guards, so
        neither path re-implements them.
        """
        key = self._subscriber_key(credential)
        client = self._clients.get(key)
        if client is None:
            # No active client → sync loop torn down while events were
            # in flight. Drop the message; the base's _subscribe_loop
            # will replay from since_token on reconnect.
            logger.debug(
                f"[matrix:{credential.agent_id}] dropping event: "
                f"no active client"
            )
            return

        # Echo filter FIRST — silent path should also drop our own
        # agent's replies before they hit the buffer.
        if await self.is_echo(message, credential):
            return

        # Compute mention flag once — authorize-event needs it as a
        # payload hint, classify reuses it via the ``mentioned`` kwarg
        # so we don't re-scan the body.
        mentioned = self._is_mentioning_us(message, credential)

        # Narra authorize-event gate. Fail-closed on anything that
        # isn't an explicit allow. Silent path is NOT exempt: it
        # writes chat_history + observations, both of which the guide
        # explicitly lists as gated operations.
        verdict = await self._authorize_event(
            credential, message, mentioned=mentioned
        )
        if not verdict.allow:
            if verdict.notice_send and verdict.notice_text:
                await self._send_matrix_notice(
                    credential, message.chat_id, verdict.notice_text
                )
            else:
                logger.debug(
                    f"[matrix:{credential.agent_id}] event denied silently "
                    f"by authorize-event (room={message.chat_id}, "
                    f"event={message.message_id})"
                )
            return

        target = await self._classify(
            client, message, credential, mentioned=mentioned
        )
        if target == "group_silent":
            await self._enqueue_silent(credential, message)
            return
        # dm / group_mention → default full-agent pipeline.
        await super()._process_message(credential, message)

    async def _build_and_run_agent(  # type: ignore[override]
        self,
        credential: NarramessengerCredential,
        message: ParsedMessage,
        sender_name: str,
        *,
        attachments: Optional[list] = None,
    ) -> str:
        """Run the base pipeline, then post the reply back to Matrix.

        Base returns the reply text (from ``extract_output``, overridden
        below to read ``send_message_to_user_directly``). Anything non-
        empty is sent via ``client.room_send``; empty means silent-not-
        reply (the agent chose not to speak) and we send NOTHING —
        posting an empty message or a placeholder would confuse humans
        in the room.
        """
        text = await super()._build_and_run_agent(
            credential, message, sender_name, attachments=attachments
        )
        if not text or not text.strip():
            logger.info(
                f"[matrix:{credential.agent_id}] agent chose silent "
                f"reply (room={message.chat_id}); nothing sent"
            )
            return text
        await self._send_matrix_reply(credential, message.chat_id, text)
        return text

    def extract_output(  # type: ignore[override]
        self, result: Any, message: ParsedMessage, credential: Any
    ) -> str:
        """Extract the reply text from the agent's ``send_message_to_user_directly``
        tool call, matching Lark / Slack / Telegram's discipline.

        The generic ``send_message_to_user_directly`` (registered by
        ChatModule) is what the agent calls to speak to the user; its
        ``content`` argument IS the reply. Returning "" here means "the
        agent did not speak this turn" — silent-not-reply — which
        prevents the base from posting the agent's internal thinking
        (``result.output_text``) into the room by mistake.

        Falls back to a scan through ``raw_items`` looking for a
        ProgressMessage or tool call whose tool_name matches; that's
        how the base ships raw agent-loop responses.
        """
        raw_items = getattr(result, "raw_items", None) or []
        for item in raw_items:
            # ProgressMessage-style objects: have .details dict with
            # tool_name + arguments.
            details = getattr(item, "details", None)
            if isinstance(details, dict):
                tool_name = details.get("tool_name") or ""
                if "send_message_to_user_directly" in str(tool_name):
                    args = details.get("arguments") or {}
                    if isinstance(args, dict):
                        content = args.get("content")
                        if isinstance(content, str) and content.strip():
                            return content
        # No explicit reply tool call → stay silent. Do NOT fall back to
        # result.output_text (agent's thinking is not for the room).
        return ""

    # ────────────────────────────────────────────────────────────────────
    # Sync loop — the piece that replaces polling
    # ────────────────────────────────────────────────────────────────────

    async def connect(  # type: ignore[override]
        self, credential: NarramessengerCredential
    ) -> AsyncIterator[dict]:
        """Drive a Matrix ``/sync`` loop, yielding one raw event per event.

        Cursor-save ordering (CRITICAL — this is what makes restart safe):

            resp = await client.sync(since=X, timeout=30s)
            for event in resp:
                yield event      # base awaits _dedup_and_handle inline
            update_since_token(next_batch)   # ONLY after ALL events yielded

        If the process dies mid-batch, the cursor stays at ``X``, the
        server replays the batch on next connect, and
        :class:`ChannelTriggerBase`'s subscriber-level dedup filters the
        already-handled events by ``event_id``. Persisting the cursor
        before the async-for completes would lose crashed events forever.

        We do NOT catch exceptions in this loop; the base's
        ``_subscribe_loop`` handles transient errors with exponential
        backoff and treats permanent auth failures via
        :meth:`is_permanent_auth_failure` → :meth:`disable_credential`.
        Our job here is: talk Matrix, yield events in causal order,
        persist the cursor once we're sure the base has consumed them.
        """
        homeserver = credential.matrix_homeserver_url
        user_id = credential.matrix_user_id
        access_token = credential.matrix_access_token
        agent_id = credential.agent_id
        key = self._subscriber_key(credential)

        if not homeserver or not user_id or not access_token:
            # Guard against a mode='matrix' row that never had
            # update_matrix_credentials() run on it. Raising here lets
            # the base disable the credential; the row is broken and
            # the owner needs to rebind.
            raise ValueError(
                f"MatrixTrigger[{agent_id}] cannot connect: missing "
                f"matrix credentials (homeserver={bool(homeserver)}, "
                f"user_id={bool(user_id)}, "
                f"access_token={bool(access_token)})"
            )

        # AsyncClientConfig: request_timeout is per-HTTP-call. Keep it a
        # bit longer than SYNC_TIMEOUT_MS so a legitimate long-poll
        # doesn't get cancelled by the client-side timeout before the
        # server returns.
        config = AsyncClientConfig(
            request_timeout=(self.SYNC_TIMEOUT_MS / 1000) + 10,
            max_timeouts=0,  # 0 = never give up; base loop owns retry
        )

        client = AsyncClient(
            homeserver=homeserver,
            user=user_id,
            device_id=credential.matrix_device_id or None,
            config=config,
        )
        # matrix-nio's login-flow methods set these; we're bypassing
        # login (we already have a token from the bind flow) so we set
        # them by hand. ``access_token`` on the client instance is what
        # gets sent as ``Authorization: Bearer ...``.
        client.access_token = access_token
        client.user_id = user_id

        self._clients[key] = client
        mgr = NarramessengerCredentialManager(self._db) if self._db else None

        logger.info(
            f"[matrix:{agent_id}] connecting to {homeserver} as {user_id}"
        )

        try:
            cursor = credential.matrix_since_token or None
            is_first_sync = cursor is None

            while self.running:
                # First sync (no cursor) uses a short server timeout so a
                # cold restart doesn't block for 30s on an idle account
                # before the base can report "connected" upstream. All
                # subsequent syncs long-poll normally.
                timeout_ms = (
                    self.FIRST_SYNC_TIMEOUT_MS
                    if is_first_sync
                    else self.SYNC_TIMEOUT_MS
                )
                resp = await client.sync(
                    timeout=timeout_ms,
                    since=cursor,
                    full_state=False,
                )
                is_first_sync = False

                # Yield each interesting event. joined rooms is the
                # primary source; invited / left rooms are Phase-4+
                # concerns (auto-accept invite, react to owner-boot).
                for room_id, room_info in resp.rooms.join.items():
                    # Consume state-block member events FIRST so the
                    # member count + display name caches are populated
                    # BEFORE any message from the same batch gets
                    # classified. On first sync this is the whole room
                    # roster; on incremental sync it's changes only.
                    state_events = getattr(
                        getattr(room_info, "state", None), "events", []
                    ) or []
                    for event in state_events:
                        if isinstance(event, RoomMemberEvent):
                            self._apply_member_event(room_id, event)

                    # Now walk the timeline. Member events here are LIVE
                    # membership changes (join/leave during this batch);
                    # apply them BEFORE the message events that follow,
                    # so classification sees the right room shape even
                    # when someone joined and immediately spoke.
                    for event in room_info.timeline.events:
                        if isinstance(event, RoomMemberEvent):
                            self._apply_member_event(room_id, event)
                            continue
                        raw = self._wrap_event(
                            event=event,
                            room_id=room_id,
                            credential=credential,
                        )
                        if raw is not None:
                            yield raw

                # ── After the async-for above returns, ALL events in
                # this batch have flowed through _dedup_and_handle in
                # the base loop. Only NOW is it safe to advance the
                # cursor; a crash between here and the next sync loses
                # nothing (we replay next_batch's precursor next time).
                #
                # Reconnect / burst flush: cold-sync backlog and post-
                # disconnect catchup should NOT wait 5s for a debounce
                # timer to fire — the backlog is already stale, get it
                # into memory now. Steady-state ticks with 0 silent
                # events skip this (drain is a no-op when buffers are
                # empty).
                if self._silent_buffer:
                    await self._drain_all_silent_buffers()

                cursor = resp.next_batch
                if mgr is not None and cursor:
                    try:
                        await mgr.update_since_token(agent_id, cursor)
                    except Exception as e:  # noqa: BLE001
                        # DB hiccup on the narrow cursor write is
                        # recoverable — worst case we replay this batch
                        # on next restart. Do NOT propagate; propagating
                        # would trigger the base's backoff+reconnect
                        # over a transient sqlite lock.
                        logger.warning(
                            f"[matrix:{agent_id}] update_since_token "
                            f"failed (non-fatal, next tick will retry): "
                            f"{e}"
                        )

                # First-sync device_id auto-persist. matrix-nio picks the
                # device from either what we set at construction or from
                # the sync response's implicit device tracking; we mirror
                # whatever it settles on into the credentials row so a
                # restart doesn't spawn a fresh device.
                if (
                    mgr is not None
                    and not credential.matrix_device_id
                    and client.device_id
                ):
                    try:
                        await mgr.update_device_id(
                            agent_id, client.device_id
                        )
                        credential.matrix_device_id = client.device_id
                        logger.info(
                            f"[matrix:{agent_id}] auto-registered "
                            f"device_id={client.device_id}"
                        )
                    except Exception as e:  # noqa: BLE001
                        logger.warning(
                            f"[matrix:{agent_id}] device_id persist "
                            f"failed (non-fatal): {e}"
                        )
        finally:
            self._clients.pop(key, None)
            try:
                await client.close()
            except Exception:  # noqa: BLE001
                pass

    # ────────────────────────────────────────────────────────────────────
    # Event → raw dict wrapper
    # ────────────────────────────────────────────────────────────────────

    def _wrap_event(
        self,
        *,
        event: Any,
        room_id: str,
        credential: NarramessengerCredential,
    ) -> Optional[dict]:
        """Marshal a matrix-nio event object into a dict for the base
        pipeline.

        Skeleton: only text messages. Commit 4 grows this to handle
        image / file / edit / reaction / redaction events explicitly.
        Anything unrecognised is dropped here (returning ``None`` skips
        the base's parse_event call).
        """
        if isinstance(event, RoomMessageText):
            return {
                "kind": "m.room.message.text",
                "event_id": event.event_id,
                "room_id": room_id,
                "sender_id": event.sender,
                "server_ts": event.server_timestamp,
                "body": event.body,
                # Attach the raw nio object too so downstream can pull
                # msgtype-specific fields (formatted_body, m.relates_to,
                # m.mentions.user_ids) without re-parsing.
                "_nio_event": event,
                # Credential surface so parse_event / is_echo don't need
                # a separate credential lookup — they get everything on
                # the raw dict.
                "_agent_id": credential.agent_id,
                "_our_user_id": credential.matrix_user_id,
            }
        # Explicit no-op for unknown types — cheap to log at debug so a
        # future "why isn't my agent seeing X" can be traced.
        logger.debug(
            f"[matrix:{credential.agent_id}] skipping event "
            f"type={type(event).__name__}"
        )
        return None

    # ────────────────────────────────────────────────────────────────────
    # parse_event (skeleton — text only; Commit 4 grows this)
    # ────────────────────────────────────────────────────────────────────

    def parse_event(  # type: ignore[override]
        self, raw: dict
    ) -> Optional[ParsedMessage]:
        """Text-only skeleton conversion.

        Commit 4 will add:
          - DM vs group detection (via room member count)
          - Mention filtering (only respond to @-mentions in groups)
          - Media msgtype → attachment_refs population
          - Own-message echo drop is done later via :meth:`is_echo` on
            the ParsedMessage; here we let all text through.
        """
        if raw.get("kind") != "m.room.message.text":
            return None

        event_id = raw.get("event_id") or ""
        room_id = raw.get("room_id") or ""
        sender_id = raw.get("sender_id") or ""
        body = raw.get("body") or ""

        if not event_id or not room_id or not sender_id:
            return None
        if not body.strip():
            return None

        # Room shape from the cache populated by _apply_member_event
        # during the sync loop. 0 (unknown) treated as GROUP so we don't
        # accidentally auto-reply to a room we can't verify — the
        # authoritative check happens again in _process_message via
        # _classify, which does an async fallback lookup on cache miss.
        member_count = self._room_member_count.get(room_id, 0)
        chat_type = ChatType.PRIVATE if member_count == 2 else ChatType.GROUP

        # Display name from the cache (populated by state events); fall
        # back to MXID until the roster arrives.
        sender_name = self._display_name_cache.get(
            (room_id, sender_id), sender_id
        )

        return ParsedMessage(
            # event_id is the natural dedup key across Matrix — same
            # event replayed after a since_token rewind hashes to the
            # same key in the base's dedup store.
            message_id=event_id,
            chat_id=room_id,
            sender_id=sender_id,
            sender_name=sender_name,
            content=body,
            chat_type=chat_type,
            timestamp_ms=int(raw.get("server_ts") or 0),
            raw=raw,
        )
