"""
@file_name: matrix_trigger.py
@date: 2026-07-02
@description: NarraMessenger MatrixTrigger — replaces the polling long-poll
              on the message plane with a real Matrix client (matrix-nio)
              talking to matrix.netmind.chat directly.

Scope of THIS FILE (Phase 1 skeleton, Commit 3):
  ✓  Class + ChannelTriggerBase wiring, all abstract methods present
  ✓  ``connect()`` implements the sync loop with the strict cursor-save
     ordering (see :meth:`connect` docstring)
  ✓  ``parse_event()`` handles ``m.room.message`` (text) minimally so
     the base pipeline can dispatch text messages end-to-end
  ✓  ``is_echo()`` filters our own agent's messages back out of the
     sync stream
  ✓  Auto-persist ``device_id`` on first sync (matrix-nio populates
     ``client.device_id`` after the server ack)

Deferred to Commit 4:
  ✗  DM / group mention routing (only "@ me" or 1:1 replies)
  ✗  ``extract_output()``: real reply sending via
     ``AsyncClient.room_send``
  ✗  Silent-not-reply fix ((stayed silent) removal)
  ✗  ``NarramessengerContextBuilder`` adapting to matrix event shape

Deferred to Phase 3:
  ✗  Multimodal: ``m.image`` / ``m.file`` / ``m.audio`` / ``m.video``
  ✗  Attachment download via authenticated /media/v1/download

Deferred to Phase 4:
  ✗  Progressive update via ``m.replace``
  ✗  Typing indicator via ``PUT /rooms/{room}/typing/{user}``

Design ref: [[Work/Narranexus/2026-07-02 NarraMessenger Matrix Adapter spec]]
"""

from __future__ import annotations

import asyncio
from typing import Any, AsyncIterator, Optional

from loguru import logger

# matrix-nio is added as a hard dep in ``pyproject.toml`` (2026-07-02).
# Non-``[e2e]`` variant on purpose — matrix.netmind.chat rooms are
# plaintext-by-default (verified 2026-07-02), so we skip libolm and its
# native-C dependency.
from nio import (
    AsyncClient,
    AsyncClientConfig,
    RoomMessageText,
    LocalProtocolError,
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
        """Fallback name resolution.

        Matrix events don't inline the sender's display name; a real
        implementation would look at the room's most recent
        ``m.room.member`` state event for this sender_id. For the
        skeleton we return the raw Matrix user_id, and Commit 4 will
        wire in a room-state-backed lookup.
        """
        return sender_id

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
                # primary source; invited / left rooms are Commit 4
                # concerns (auto-accept invite, react to owner-boot).
                for room_id, room_info in resp.rooms.join.items():
                    for event in room_info.timeline.events:
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

        return ParsedMessage(
            # event_id is the natural dedup key across Matrix — same
            # event replayed after a since_token rewind hashes to the
            # same key in the base's dedup store.
            message_id=event_id,
            chat_id=room_id,
            sender_id=sender_id,
            sender_name=sender_id,  # display-name lookup deferred to Commit 4
            content=body,
            # Skeleton assumes PRIVATE (DM) — Commit 4 flips to GROUP
            # when the room has ≥3 members and only responds when
            # mentioned. Assuming DM for now keeps a smoke test simple
            # (owner ↔ agent 1:1) without triggering group filters.
            chat_type=ChatType.PRIVATE,
            timestamp_ms=int(raw.get("server_ts") or 0),
            raw=raw,
        )
