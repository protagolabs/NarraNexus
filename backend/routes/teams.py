"""
@file_name: teams.py
@author: NetMind.AI
@date: 2026-05-08
@description: REST API for team membership management

Subproject 1: Team Membership

Endpoints (all under /api/teams):
- GET    /                       List teams owned by current user
- POST   /                       Create a team
- GET    /{team_id}              Get one team with members
- PATCH  /{team_id}              Update team metadata
- DELETE /{team_id}              Delete team (members are unlinked, agents kept)
- POST   /{team_id}/members      Add agent to team
- DELETE /{team_id}/members/{agent_id}  Remove agent from team
"""

import mimetypes
import shutil
from typing import Optional

from fastapi import APIRouter, File, HTTPException, Query, Request, UploadFile
from loguru import logger
from pydantic import BaseModel

from xyz_agent_context.utils import format_for_api
from xyz_agent_context.utils.db.db_factory import get_db_client
from xyz_agent_context.utils.mime_sniff import sniff_mime_type
from xyz_agent_context.repository import TeamRepository, TeamMemberRepository
from xyz_agent_context.repository.user_repository import UserRepository
from xyz_agent_context.message_bus.local_bus import LocalMessageBus
from xyz_agent_context.message_bus.attachments import (
    load_bus_attachment_meta,
    resolve_shared_file_for_user,
    store_bus_attachment_meta,
    store_bytes_into_bus,
)
from xyz_agent_context.schema.attachment_schema import derive_category_from_mime
from xyz_agent_context.utils.workspace_paths import team_shared_dir
from xyz_agent_context.repository.team_workspace_repository import (
    ArtifactHistoryRepository,
    TeamFileRepository,
)
from xyz_agent_context.repository.artifact_repository import ArtifactRepository
from xyz_agent_context.repository.team_bulletin_repository import TeamBulletinRepository
from xyz_agent_context.schema.team_schema import (
    BULLETIN_MAX_ENTRIES,
    BULLETIN_MAX_ENTRY_CHARS,
    BULLETIN_MAX_TOTAL_CHARS,
    BULLETIN_SOURCE_USER,
    BULLETIN_TIER_CURRENT_TASK,
    BULLETIN_TIER_LONG_TERM,
    BulletinUsage,
    CreateTeamRequest,
    UpdateTeamRequest,
    AddMemberRequest,
    TeamWithMembers,
    TeamListResponse,
    TeamOperationResponse,
)
from backend.auth import resolve_current_user_id


router = APIRouter()


async def _user_id_for_request(request: Request) -> str:
    # Unified across cloud (JWT) and local (X-User-Id header) modes —
    # auth_middleware populates request.state.user_id either way, so
    # downstream filtering is identical. See backend/auth.py for the
    # mode-specific identity source.
    return await resolve_current_user_id(request)


# --- Team group chat (over the message bus) --------------------------------
#
# A team's group chat is a single message-bus group channel whose
# ``created_by`` is the synthetic marker ``team_<team_id>`` (NOT an agent), so:
#   * the channel is found deterministically (no extra schema/column), and
#   * no member agent is the "channel owner", which in MessageBusTrigger is
#     always activated by any message — here delivery is purely @-mention
#     driven (the user @mentions agents; @all maps to the bus "@everyone").
# The user posts as the synthetic sender ``usr_<user_id>``. The standalone
# MessageBusTrigger picks the message up and runs the @mentioned agents; their
# replies post back into the same channel (see message_bus_trigger.py).

TEAM_ROOM_OWNER_PREFIX = "team_"
USER_SENDER_PREFIX = "usr_"


class TeamChatSendRequest(BaseModel):
    """User message into a team group chat. ``mentions`` carries agent_ids
    and/or the literal ``"@all"`` (mapped to the bus "@everyone").

    ``attachments`` are bus-attachment dicts returned by
    ``POST /{team_id}/chat/attachments`` (each carries a ``rel_path`` into the
    user's shared area); they are re-validated server-side before the send."""

    content: str
    mentions: list[str] = []
    attachments: list[dict] = []


def _resolve_default_responder(team, member_agent_ids: list[str]) -> str | None:
    """The agent that answers a team message with NO @mention.

    ``team.lead_agent_id`` if it's set and still a member; otherwise the
    earliest-joined member (``member_agent_ids`` is ordered by join time). A
    single-agent team therefore auto-responds. Returns None for an empty team.
    """
    if not member_agent_ids:
        return None
    lead = getattr(team, "lead_agent_id", None)
    if lead and lead in member_agent_ids:
        return lead
    return member_agent_ids[0]


async def _wipe_team_data(
    db,
    team,
    *,
    clear_chat: bool,
    clear_files: bool,
    clear_artifacts: bool = False,
    clear_bulletin: bool = False,
) -> dict:
    """Clear a team's group-chat history and/or its shared files.

    The team counterpart to ``wipe_agent_data``: it clears the collaboration
    *surface* (the room's messages + the ``_shared/teams/{team_id}`` folder)
    but KEEPS the team, its members, and the bus channel + membership rows so
    the room keeps working. Owner scoping is enforced by the caller.

    - clear_chat: delete ``bus_messages`` (and their ``bus_message_failures``)
      for the team room channel (``created_by='team_<id>'``).
    - clear_files: delete the on-disk ``_shared/teams/{team_id}`` dir, its
      ``team_files`` index rows, AND this team's artifacts.

      The cascade to artifacts is not an over-reach — it became correct when
      registration started REQUIRING a team artifact to live in that folder.
      An earlier version of this docstring argued the opposite, on the grounds
      that a team artifact pointed into the producer's own workspace and
      survived; that was true then and is false now. Deleting the folder now
      destroys every team artifact's content, so leaving the rows behind would
      list artifacts whose files are gone. ``ArtifactService.heal`` does not
      rescue them either — not because it is team-blind (it is not; it scopes
      itself to the team folder), but because it only ever RECONNECTS a pointer
      to a file that still exists, and this deletes the files themselves.
    - clear_artifacts: delete this team's ``instance_artifacts`` rows and their
      attribution history WITHOUT touching the folder. Used when the TEAM
      itself goes away, and available on its own for dropping the tabs while
      keeping the files.
    - clear_bulletin: delete the team's standing rules and its auto-summary.
      Deliberately its OWN scope and not folded into ``clear_chat``: the
      bulletin exists precisely because it is not chat, so wiping the
      transcript must not take the rules with it — that would recreate the
      "say it again" loop the bulletin was built to end.

    DB deletes commit first (source of truth); the disk delete runs after,
    best-effort, so a filesystem hiccup never rolls back the DB. Idempotent.
    """
    result = {
        "chat_messages": 0,
        "chat_failures": 0,
        "files_removed": False,
        "file_rows": 0,
        "artifacts": 0,
        "bulletin_entries": 0,
        "errors": [],
    }
    marker = f"{TEAM_ROOM_OWNER_PREFIX}{team.team_id}"

    if clear_chat:
        channel = await db.get_one("bus_channels", {"created_by": marker, "channel_type": "group"})
        if channel:
            cid = channel["channel_id"]
            # One IN-subquery statement instead of pulling every message_id
            # into memory and deleting failures row-by-row (N+1 in an open
            # transaction). Bare identifiers — the raw SQL must stay portable
            # across the sqlite and MySQL dialects.
            async with db.transaction():
                failures = await db.execute(
                    "DELETE FROM bus_message_failures WHERE message_id IN "
                    "(SELECT message_id FROM bus_messages WHERE channel_id = %s)",
                    (cid,),
                    fetch=False,
                )
                result["chat_failures"] = failures if isinstance(failures, int) else 0
                result["chat_messages"] = await db.delete("bus_messages", {"channel_id": cid})

    if clear_files:
        # The index has to go with the bytes. A row that outlives its file is
        # worse than no row: the panel still lists it and the user only finds
        # out when the download fails. Since team artifacts are required to
        # live in this very folder, the same argument now covers them — see the
        # docstring for why the earlier reasoning stopped holding.
        result["file_rows"] = await TeamFileRepository(db).delete_by_team(team.team_id)
        clear_artifacts = True

        d = team_shared_dir(team.owner_user_id, team.team_id)
        try:
            if d.exists():
                shutil.rmtree(d)
                result["files_removed"] = True
        except Exception as e:  # noqa: BLE001 — best-effort; never fail the wipe
            result["errors"].append(f"files: {e}")
            logger.warning(f"[team wipe] failed to delete shared dir {d}: {e}")

    if clear_artifacts:
        # Attribution rows first: an orphaned history row points at an
        # artifact_id nothing can resolve. Filtered by THIS team, so private
        # work and other teams the same owner has are untouched.
        async with db.transaction():
            arts = await db.execute(
                "SELECT artifact_id FROM instance_artifacts WHERE team_id = %s",
                (team.team_id,),
                fetch=True,
            )
            await ArtifactHistoryRepository(db).delete_for_artifacts([row["artifact_id"] for row in arts or []])
            result["artifacts"] = await db.delete("instance_artifacts", {"team_id": team.team_id})

    if clear_bulletin:
        result["bulletin_entries"] = await TeamBulletinRepository(db).delete_for_team(team.team_id)

    logger.info(
        f"[team wipe] team={team.team_id} chat={clear_chat} files={clear_files} "
        f"messages={result['chat_messages']} files_removed={result['files_removed']}"
    )
    return result


async def _get_or_create_team_room(
    db, bus: LocalMessageBus, team_id: str, team_name: str, member_agent_ids: list[str]
) -> str:
    """Find (or create) the team's group-chat channel and sync its members to
    the team's current agents. Returns the channel_id."""
    marker = f"{TEAM_ROOM_OWNER_PREFIX}{team_id}"
    existing = await db.get_one("bus_channels", {"created_by": marker, "channel_type": "group"})
    if existing:
        channel_id = existing["channel_id"]
    else:
        # create_channel sets created_by = members[0]; immediately rewrite it to
        # the non-agent marker so no member is the always-activated owner.
        channel_id = await bus.create_channel(
            name=team_name or "Team",
            members=list(member_agent_ids),
            channel_type="group",
        )
        await db.update("bus_channels", {"channel_id": channel_id}, {"created_by": marker})

    # Sync membership to the team's current agents (add missing, drop extras).
    current = {m.agent_id for m in await bus.get_channel_members(channel_id)}
    target = set(member_agent_ids)
    for aid in target - current:
        await bus.join_channel(aid, channel_id)
    for aid in current - target:
        await bus.leave_channel(aid, channel_id)

    return channel_id


def _sanitized_attachment(user_id: str, att: object) -> dict | None:
    """Rebuild one echoed attachment dict from server-side state only.

    The client echoes dicts from the upload endpoint, so every field is
    attacker-writable over the wire — and the dict lands verbatim in
    ``bus_messages.attachments`` and (via ``build_bus_markers``) in the team
    prompt, with ``transcript`` injected as raw text. So nothing echoed is
    trusted: the ``rel_path`` only serves to locate the file (gated to the
    sender's own shared area), then the dict is reloaded from the meta
    sidecar the upload endpoint wrote (``load_bus_attachment_meta``). No
    sidecar → minimal metadata rebuilt from disk, no transcript.
    """
    if not isinstance(att, dict):
        return None
    rel_path = att.get("rel_path")
    if not isinstance(rel_path, str) or not rel_path:
        return None
    on_disk = resolve_shared_file_for_user(user_id, rel_path)
    if on_disk is None:
        return None
    meta = load_bus_attachment_meta(user_id, rel_path)
    if meta is not None:
        return meta
    guessed, _ = mimetypes.guess_type(on_disk.name)
    mime = guessed or "application/octet-stream"
    return {
        "file_id": on_disk.stem,
        "original_name": on_disk.name,
        "mime_type": mime,
        "size_bytes": on_disk.stat().st_size,
        "category": derive_category_from_mime(mime).value,
        "rel_path": rel_path,
    }


@router.post("/{team_id}/chat/messages")
async def send_team_chat(team_id: str, payload: TeamChatSendRequest, request: Request):
    user_id = await _user_id_for_request(request)

    valid_attachments = [
        sanitized
        for att in (payload.attachments or [])
        if (sanitized := _sanitized_attachment(user_id, att)) is not None
    ]

    if not (payload.content or "").strip() and not valid_attachments:
        raise HTTPException(status_code=400, detail="Message content or an attachment is required")

    db = await get_db_client()
    team_repo = TeamRepository(db)
    member_repo = TeamMemberRepository(db)

    team = await team_repo.get_team(team_id)
    if not team:
        raise HTTPException(status_code=404, detail="Team not found")
    if team.owner_user_id != user_id:
        raise HTTPException(status_code=403, detail="Forbidden")

    members = await member_repo.list_members_by_team(team_id)
    bus = LocalMessageBus(backend=db._backend)
    channel_id = await _get_or_create_team_room(db, bus, team_id, team.name, members)

    # Map the UI's "@all" to the bus-native "@everyone"; pass agent_ids through.
    resolved = ["@everyone" if m == "@all" else m for m in (payload.mentions or [])]
    # No @mention → route to the team's default responder so the room never
    # goes silent. Exactly one agent is triggered; it can @-delegate from there.
    if not resolved:
        default_responder = _resolve_default_responder(team, members)
        if default_responder:
            resolved = [default_responder]
    msg_id = await bus.send_message(
        from_agent=f"{USER_SENDER_PREFIX}{user_id}",
        to_channel=channel_id,
        content=payload.content.strip(),
        mentions=resolved or None,
        attachments=valid_attachments or None,
    )
    logger.info(f"Team chat: user {user_id} -> team {team_id} channel {channel_id} (mentions={resolved})")
    return {"success": True, "message_id": msg_id, "channel_id": channel_id}


@router.post("/{team_id}/chat/attachments")
async def upload_team_chat_attachment(
    team_id: str,
    request: Request,
    source: str | None = Query(
        None,
        description="'recording' = in-browser voice memo (rendered as a transcript); "
        "any other value = regular file upload. Whisper runs for all audio/* either way.",
    ),
    file: UploadFile = File(..., description="File to attach to a team chat message"),
):
    """Store a user-uploaded file into the sender's shared bus area and return a
    bus-attachment dict. The client echoes that dict back in the ``attachments``
    field of ``POST /{team_id}/chat/messages``. The file lands in
    ``{base}/{user_id}/_shared/bus_files`` so every team agent can Read it. For
    audio uploads we run Whisper (same as the single-agent path) so @mentioned
    agents receive the spoken content as text via the attachment marker."""
    from backend.config import settings as backend_settings

    user_id = await _user_id_for_request(request)
    db = await get_db_client()
    team = await TeamRepository(db).get_team(team_id)
    if not team:
        raise HTTPException(status_code=404, detail="Team not found")
    if team.owner_user_id != user_id:
        raise HTTPException(status_code=403, detail="Forbidden")

    raw_bytes = await file.read()
    max_bytes = backend_settings.max_upload_bytes
    if len(raw_bytes) > max_bytes:
        raise HTTPException(
            status_code=413,
            detail=f"File exceeds the maximum upload size of {max_bytes // (1024 * 1024)} MB",
        )

    mime_type = sniff_mime_type(raw_bytes, filename=file.filename or "", client_type=file.content_type)
    att = await store_bytes_into_bus(
        user_id=user_id,
        raw_bytes=raw_bytes,
        original_name=file.filename or "upload",
        mime_type=mime_type,
    )

    # Normalise source for deterministic frontend dispatch (recording vs upload).
    att["source"] = "recording" if source == "recording" else "upload"

    # Transcribe audio uploads so team agents get the words (they can't listen).
    transcription_available: bool | None = None
    if mime_type.startswith("audio/"):
        from xyz_agent_context.agent_framework.llm.transcription import TranscriptionService

        on_disk = resolve_shared_file_for_user(user_id, att["rel_path"])
        svc = TranscriptionService.instance()
        transcription_available = await svc.is_available(user_id)
        if transcription_available and on_disk is not None:
            transcript = await svc.transcribe(
                file_path=str(on_disk),
                file_id=att["file_id"],
                agent_id="",  # team memo has no single agent; public endpoint falls back to shared resolver
                user_id=user_id,
            )
            if transcript:
                att["transcript"] = transcript
                logger.info(f"Team voice memo transcribed: file={att['file_id']} chars={len(transcript)}")

    # Persist the finished dict server-side: the send endpoint reloads it from
    # this sidecar instead of trusting the client's echoed copy.
    store_bus_attachment_meta(user_id, att)

    logger.info(f"Team chat upload: user {user_id} team {team_id} file={att['file_id']} mime={mime_type}")
    return {"success": True, "attachment": att, "transcription_available": transcription_available}


@router.get("/{team_id}/chat/messages")
async def get_team_chat(team_id: str, request: Request, since: str | None = None):
    user_id = await _user_id_for_request(request)
    db = await get_db_client()
    team_repo = TeamRepository(db)
    member_repo = TeamMemberRepository(db)

    team = await team_repo.get_team(team_id)
    if not team:
        raise HTTPException(status_code=404, detail="Team not found")
    if team.owner_user_id != user_id:
        raise HTTPException(status_code=403, detail="Forbidden")

    members = await member_repo.list_members_by_team(team_id)
    bus = LocalMessageBus(backend=db._backend)
    channel_id = await _get_or_create_team_room(db, bus, team_id, team.name, members)

    messages = await bus.get_messages(channel_id, since=since, limit=200)

    # Resolve sender display names: agents -> agent_name; usr_<id> -> the user.
    agent_rows = await db.get_by_ids("agents", "agent_id", members) if members else []
    name_by_agent = {r["agent_id"]: (r.get("agent_name") or r["agent_id"]) for r in agent_rows if r}
    user_name = await UserRepository(db).get_display_name(user_id)

    out = []
    for m in messages:
        is_user = m.from_agent.startswith(USER_SENDER_PREFIX)
        out.append(
            {
                "message_id": m.message_id,
                "from_agent": m.from_agent,
                "author_name": (user_name or "You") if is_user else name_by_agent.get(m.from_agent, m.from_agent),
                "is_user": is_user,
                "content": m.content,
                "attachments": m.attachments,
                # "text"/"multimodal" for ordinary messages; "system_stop" marks the
                # stop notice, which the frontend renders as a system line (from an
                # i18n key) rather than as this agent speaking.
                "msg_type": m.msg_type,
                # Turn that produced this reply (None for user messages / legacy
                # rows) — powers the per-message reasoning disclosure.
                "event_id": m.event_id,
                "created_at": format_for_api(m.created_at),
            }
        )

    activity = await _member_activity(db, bus, channel_id, members)

    return {
        "success": True,
        "channel_id": channel_id,
        "messages": out,
        "activity": activity,
        "lead_agent_id": _resolve_default_responder(team, members),
    }


async def _member_activity(db, bus, channel_id: str, members: list[str]) -> list[dict]:
    """Per-member live status for the team activity console.

    Four states, deliberately distinct — collapsing the last two is what made a
    wedged bus look identical to a busy one:

    * ``running``  — the trigger is running it and the heartbeat is fresh;
      carries the live phase, tool count and the turn's step timeline.
    * ``stalled``  — the row still claims running but no heartbeat has landed
      within ``ACTIVITY_STALE_SECONDS``. The turn DID start; we stopped hearing
      from it. Surfaced as its own state so the UI can say so instead of
      silently showing "queued".
    * ``queued``   — an unprocessed @mention is waiting in this room but no turn
      has started (poll latency, a busy worker slot, or the agent's own turn is
      still ahead of it). Carries how long it has been waiting.
    * ``idle``     — nothing pending. Still carries the PREVIOUS turn's step
      timeline plus when it ended, so the room can show what an agent just did.
    """
    from xyz_agent_context.message_bus import activity as bus_activity

    act_rows = {r["agent_id"]: r for r in await bus_activity.get_channel_activity(db, channel_id)}
    try:
        pending = await bus.get_room_pending_summary(channel_id, members)
    except Exception as e:  # noqa: BLE001 — best-effort indicator, never fail the GET
        logger.warning(f"Team chat pending summary failed for {channel_id}: {e}")
        pending = {}

    out: list[dict] = []
    for aid in members:
        row = act_rows.get(aid)
        steps = bus_activity.parse_steps(row)
        waiting = pending.get(aid)

        if row is not None and bus_activity.is_live(row):
            status = "running"
        elif row is not None and bus_activity.is_stalled(row):
            status = "stalled"
        elif waiting:
            status = "queued"
        else:
            status = "idle"

        entry: dict = {"agent_id": aid, "status": status}
        if status in ("running", "stalled"):
            entry.update(
                {
                    "phase": row.get("phase"),
                    "tool_count": row.get("tool_count") or 0,
                    "started_at": format_for_api(row.get("started_at")),
                    # The last heartbeat. For `stalled` this is what the UI counts
                    # "no signal for N minutes" from.
                    "last_signal_at": format_for_api(row.get("updated_at")),
                    "steps": steps,
                    # The current turn's events-row id, once note_event_id() has
                    # bound it — lets the frontend fetch the full event_log via
                    # the existing event-log endpoint.
                    "event_id": row.get("event_id"),
                }
            )
        elif status == "queued":
            entry.update(
                {
                    "queued_count": waiting["count"],
                    "queued_since": format_for_api(waiting["oldest_at"]),
                }
            )
            if row is not None:
                entry["event_id"] = row.get("event_id")
        elif row is not None and steps["items"]:
            # Idle, but we still hold the trace of the turn it just finished.
            entry.update(
                {
                    # started_at→finished_at is where the roster's "ran Ns" comes
                    # from; omitting the start made every finished turn read "0s".
                    "started_at": format_for_api(row.get("started_at")),
                    "finished_at": format_for_api(row.get("updated_at")),
                    "steps": steps,
                    "tool_count": row.get("tool_count") or 0,
                    "event_id": row.get("event_id"),
                }
            )
        out.append(entry)
    return out


@router.get("", response_model=TeamListResponse)
async def list_teams(request: Request):
    user_id = await _user_id_for_request(request)
    db = await get_db_client()
    team_repo = TeamRepository(db)
    member_repo = TeamMemberRepository(db)

    teams = await team_repo.list_teams_by_owner(user_id)
    enriched: list[TeamWithMembers] = []
    for t in teams:
        members = await member_repo.list_members_by_team(t.team_id)
        enriched.append(TeamWithMembers(team=t, member_agent_ids=members))
    return TeamListResponse(teams=enriched)


@router.post("", response_model=TeamOperationResponse)
async def create_team(payload: CreateTeamRequest, request: Request):
    user_id = await _user_id_for_request(request)
    if not payload.name or not payload.name.strip():
        raise HTTPException(status_code=400, detail="Team name is required")

    db = await get_db_client()
    team_repo = TeamRepository(db)
    team = await team_repo.create_team(
        owner_user_id=user_id,
        name=payload.name.strip(),
        description=payload.description,
        color=payload.color,
    )
    logger.info(f"Team created: {team.team_id} by {user_id}")
    return TeamOperationResponse(success=True, team=team, message="Team created")


@router.get("/{team_id}", response_model=TeamOperationResponse)
async def get_team(team_id: str, request: Request):
    user_id = await _user_id_for_request(request)
    db = await get_db_client()
    team_repo = TeamRepository(db)
    team = await team_repo.get_team(team_id)
    if not team:
        raise HTTPException(status_code=404, detail="Team not found")
    if team.owner_user_id != user_id:
        raise HTTPException(status_code=403, detail="Forbidden")
    return TeamOperationResponse(success=True, team=team)


@router.patch("/{team_id}", response_model=TeamOperationResponse)
async def update_team(team_id: str, payload: UpdateTeamRequest, request: Request):
    user_id = await _user_id_for_request(request)
    db = await get_db_client()
    team_repo = TeamRepository(db)
    team = await team_repo.get_team(team_id)
    if not team:
        raise HTTPException(status_code=404, detail="Team not found")
    if team.owner_user_id != user_id:
        raise HTTPException(status_code=403, detail="Forbidden")

    updates = {k: v for k, v in payload.model_dump(exclude_none=True).items()}
    # Default-responder: a non-empty lead must be a current member; an empty
    # string clears it (back to the earliest-joined fallback). exclude_none
    # already drops a null, so "" is the wire signal for "clear".
    if "lead_agent_id" in updates:
        lead = (updates["lead_agent_id"] or "").strip()
        if lead:
            members = await TeamMemberRepository(db).list_members_by_team(team_id)
            if lead not in members:
                raise HTTPException(status_code=400, detail="lead_agent_id must be a team member")
        updates["lead_agent_id"] = lead or None
    if updates:
        await team_repo.update_team(team_id, updates)
    refreshed = await team_repo.get_team(team_id)
    return TeamOperationResponse(success=True, team=refreshed, message="Team updated")


@router.delete("/{team_id}", response_model=TeamOperationResponse)
async def delete_team(team_id: str, request: Request):
    user_id = await _user_id_for_request(request)
    db = await get_db_client()
    team_repo = TeamRepository(db)
    member_repo = TeamMemberRepository(db)

    team = await team_repo.get_team(team_id)
    if not team:
        raise HTTPException(status_code=404, detail="Team not found")
    if team.owner_user_id != user_id:
        raise HTTPException(status_code=403, detail="Forbidden")

    # Take the workspace with it. Once the team row is gone its artifacts are
    # unreachable by EVERY query path — the private surfaces exclude them
    # (team_id IS NULL), list_by_team needs a team that no longer exists, and
    # list_for_agent_context joins team_members, which the next line empties.
    # Rows nothing can ever read again are precisely the orphans the acceptance
    # criterion is about; leaving them is not "harmless clutter". The bulletin
    # is in the same position: its only reader is this team's turn builder.
    await _wipe_team_data(
        db,
        team,
        clear_chat=True,
        clear_files=True,
        clear_artifacts=True,
        clear_bulletin=True,
    )
    await member_repo.remove_all_members(team_id)
    await team_repo.delete_team(team_id)
    return TeamOperationResponse(success=True, message="Team deleted")


@router.delete("/{team_id}/data")
async def clear_team_data(
    team_id: str,
    request: Request,
    chat: bool = Query(True, description="Delete the team room's group-chat messages"),
    files: bool = Query(
        True,
        description="Delete the team's shared files AND its artifacts — team artifacts live in this folder, so removing it destroys their content",
    ),
    bulletin: bool = Query(
        False,
        description="Delete the team's bulletin — its standing rules and auto-summary",
    ),
):
    """Clear a team's collaboration data (chat and/or shared files), keeping the
    team, its members, and the bus channel. Owner-only. The team counterpart to
    the per-agent ``DELETE /{agent_id}/history``."""
    if not chat and not files and not bulletin:
        raise HTTPException(status_code=400, detail="Select at least one scope: chat, files and/or bulletin")

    user_id = await _user_id_for_request(request)
    db = await get_db_client()
    team = await TeamRepository(db).get_team(team_id)
    if not team:
        raise HTTPException(status_code=404, detail="Team not found")
    if team.owner_user_id != user_id:
        raise HTTPException(status_code=403, detail="Forbidden")

    result = await _wipe_team_data(db, team, clear_chat=chat, clear_files=files, clear_bulletin=bulletin)
    return {"success": True, **result}


# --- Team workspace (artifacts + shared files) -----------------------------
#
# The read surface for the team room's workspace panel. Both are owner-scoped
# through the same check every other team route uses: a team belongs to one
# user (the bus forbids cross-user messaging, so a team cannot span users),
# which makes the owner the tenant boundary here too.


async def _team_files(db, team_id: str) -> list[dict]:
    """A team's shared files, newest first. Thin wrapper so the route reads
    naturally and the test can call it without an HTTP client."""
    return await TeamFileRepository(db).list_by_team(team_id)


# --- Team bulletin ---------------------------------------------------------
#
# The standing rules every member loads on every team turn. Everything here
# reaches every prompt, which is why the budget below is enforced rather than
# advisory: an unbounded bulletin is an unbounded prompt on the hottest path
# the team feature has.


class BulletinLimitExceeded(Exception):
    """A write that would breach the bulletin's budget.

    Carries the reason as its message so the caller — the REST route for the
    user, the MCP tool for an agent — can hand the same explanation to a very
    different reader without re-deriving it.
    """


async def check_bulletin_budget(repo: TeamBulletinRepository, team_id: str) -> BulletinUsage:
    """Current usage of the shared entry budget (summary excluded).

    Exposed so the UI can grey out "add" BEFORE the user types a rule, rather
    than rejecting it after they have written it.
    """
    return await repo.usage(team_id)


async def add_bulletin_entry(
    repo: TeamBulletinRepository,
    *,
    team_id: str,
    content: str,
    source: str,
    author_id: Optional[str],
    tier: str = BULLETIN_TIER_LONG_TERM,
):
    """Add one bulletin entry, or refuse and say why.

    REFUSE, never trim. A silently shortened rule is worse than a rejected
    one: the user is told nothing and goes on believing the whole rule is in
    force, and the half that survived may invert the meaning of the half that
    did not ("never share X with Y" cut at "never share X"). Every refusal
    names the limit it hit, because "too long" without a number leaves the
    user guessing how much to cut.

    The budget is shared between the user and the agents. The prompt does not
    care who wrote a line, so neither does the ceiling — otherwise an agent
    could mint itself room the user cannot.
    """
    text = (content or "").strip()
    if not text:
        raise BulletinLimitExceeded("A bulletin entry cannot be empty.")
    if len(text) > BULLETIN_MAX_ENTRY_CHARS:
        raise BulletinLimitExceeded(
            f"That entry is {len(text)} characters; the limit is "
            f"{BULLETIN_MAX_ENTRY_CHARS}. Shorten it to the rule itself."
        )

    usage = await repo.usage(team_id)
    if usage.entry_count >= BULLETIN_MAX_ENTRIES:
        raise BulletinLimitExceeded(
            f"The bulletin already holds {usage.entry_count} entries "
            f"(limit {BULLETIN_MAX_ENTRIES}). Remove one before adding another."
        )
    if usage.total_chars + len(text) > BULLETIN_MAX_TOTAL_CHARS:
        raise BulletinLimitExceeded(
            f"The bulletin would reach {usage.total_chars + len(text)} characters "
            f"(limit {BULLETIN_MAX_TOTAL_CHARS}). Shorten or remove an entry first."
        )

    return await repo.add(team_id=team_id, content=text, source=source, author_id=author_id, tier=tier)


# Marks a bulletin change in the transcript. Same shape as the stop notice
# (``runs.py``): the frontend renders it from an i18n key because the database
# cannot know the reader's language, and ``content`` carries an English
# fallback for consumers that only read text.
BULLETIN_NOTICE_MSG_TYPE = "system_bulletin"


async def _post_bulletin_notice(db, team_id: str, action: str, actor: str = "") -> None:
    """Leave a trace in the room when the bulletin changes.

    The bulletin governs every reply from now on, so a silent change means
    members start behaving differently with nothing in the transcript to
    explain why — the same reasoning that made a stopped run post a notice.

    NO MENTIONS, deliberately. A mention would wake every member the instant a
    rule is written, and a rule written BY an agent would then wake the agents
    that might write another — the write→wake→write loop. The notice is for
    the humans reading the room; the agents pick the rules up on their next
    turn regardless, which is the entire point of the bulletin.

    Best-effort: a bulletin write that succeeded must not report failure
    because its announcement did not land.
    """
    try:
        team = await TeamRepository(db).get_team(team_id)
        if not team:
            return
        members = await TeamMemberRepository(db).list_members_by_team(team_id)
        bus = LocalMessageBus(backend=db._backend)
        channel_id = await _get_or_create_team_room(db, bus, team_id, team.name, members)
        await bus.send_message(
            from_agent=actor or f"{USER_SENDER_PREFIX}{team.owner_user_id}",
            to_channel=channel_id,
            content=f"Team bulletin {action}.",
            msg_type=BULLETIN_NOTICE_MSG_TYPE,
            mentions=None,
        )
    except Exception as e:  # noqa: BLE001
        logger.warning(f"[team-bulletin] could not post notice for {team_id}: {e}")


class CreateBulletinEntryRequest(BaseModel):
    content: str
    tier: str = BULLETIN_TIER_LONG_TERM


class UpdateBulletinEntryRequest(BaseModel):
    content: str


@router.get("/{team_id}/bulletin")
async def list_team_bulletin(team_id: str, request: Request):
    """The team's bulletin plus its current budget usage.

    Usage rides along with the list so the panel can grey out "add" BEFORE the
    user types a rule, instead of rejecting it once written.
    """
    db, _team = await _require_team_owner(request, team_id)
    repo = TeamBulletinRepository(db)
    entries = await repo.list_for_team(team_id)
    usage = await check_bulletin_budget(repo, team_id)
    return {
        "entries": [format_for_api(e.model_dump()) for e in entries],
        "usage": usage.model_dump(),
        "limits": {
            "max_entries": BULLETIN_MAX_ENTRIES,
            "max_entry_chars": BULLETIN_MAX_ENTRY_CHARS,
            "max_total_chars": BULLETIN_MAX_TOTAL_CHARS,
        },
    }


@router.post("/{team_id}/bulletin")
async def create_team_bulletin_entry(team_id: str, payload: CreateBulletinEntryRequest, request: Request):
    """Add a rule. Over-budget is a 400 that names the limit — never a trim."""
    db, _team = await _require_team_owner(request, team_id)
    user_id = await _user_id_for_request(request)
    tier = BULLETIN_TIER_CURRENT_TASK if payload.tier == BULLETIN_TIER_CURRENT_TASK else BULLETIN_TIER_LONG_TERM
    try:
        entry = await add_bulletin_entry(
            TeamBulletinRepository(db),
            team_id=team_id,
            content=payload.content,
            source=BULLETIN_SOURCE_USER,
            author_id=user_id,
            tier=tier,
        )
    except BulletinLimitExceeded as e:
        raise HTTPException(status_code=400, detail=str(e))
    await _post_bulletin_notice(db, team_id, "updated")
    return {"success": True, "entry": format_for_api(entry.model_dump())}


@router.patch("/{team_id}/bulletin/{entry_id}")
async def update_team_bulletin_entry(
    team_id: str, entry_id: str, payload: UpdateBulletinEntryRequest, request: Request
):
    """Edit any entry — the owner's authority covers agent-written rules and
    the auto-summary alike. An agent editing its own goes through the tool."""
    db, _team = await _require_team_owner(request, team_id)
    try:
        entry = await edit_bulletin_entry(
            TeamBulletinRepository(db),
            team_id=team_id,
            entry_id=entry_id,
            content=payload.content,
        )
    except BulletinLimitExceeded as e:
        raise HTTPException(status_code=400, detail=str(e))
    if entry is None:
        raise HTTPException(status_code=404, detail="Bulletin entry not found")
    await _post_bulletin_notice(db, team_id, "updated")
    return {"success": True}


@router.delete("/{team_id}/bulletin/{entry_id}")
async def delete_team_bulletin_entry(team_id: str, entry_id: str, request: Request):
    """Remove one entry. The owner may remove anything, including what an agent
    wrote and the auto-summary — that is the escape hatch that makes agent
    write access safe to grant."""
    db, _team = await _require_team_owner(request, team_id)
    repo = TeamBulletinRepository(db)
    entry = await repo.get(entry_id)
    if entry is None or entry.team_id != team_id:
        raise HTTPException(status_code=404, detail="Bulletin entry not found")
    await repo.delete(entry_id)
    await _post_bulletin_notice(db, team_id, "updated")
    return {"success": True}


@router.delete("/{team_id}/bulletin")
async def clear_team_bulletin_tier(
    team_id: str,
    request: Request,
    tier: str = Query(
        BULLETIN_TIER_CURRENT_TASK,
        description="Which tier to clear; the auto-summary belongs to no tier and survives",
    ),
):
    """Clear one tier in a single action — the "this task is done" button.

    Deliberately tier-scoped rather than a blanket clear: the standing rules
    are the part the user least wants to retype, and they are what the whole
    feature exists to stop retyping.
    """
    db, _team = await _require_team_owner(request, team_id)
    if tier not in (BULLETIN_TIER_CURRENT_TASK, BULLETIN_TIER_LONG_TERM):
        raise HTTPException(status_code=400, detail="Unknown bulletin tier")
    removed = await TeamBulletinRepository(db).delete_tier(team_id, tier)
    if removed:
        await _post_bulletin_notice(db, team_id, "cleared")
    return {"success": True, "removed": removed}


async def edit_bulletin_entry(repo: TeamBulletinRepository, *, team_id: str, entry_id: str, content: str):
    """Replace one entry's text, or refuse and say why.

    The projected total SUBTRACTS the text being replaced. Counting the new
    text against a total that still includes the old one would refuse the very
    edit that relieves the budget: a user at the ceiling would be told to free
    space by doing exactly what they were just blocked from doing.

    Returns the entry as it now reads.
    """
    entry = await repo.get(entry_id)
    # Scoped to the team, not just the id: an entry_id belonging to another of
    # the owner's teams must not be reachable through this one's path.
    if entry is None or entry.team_id != team_id:
        return None

    text = (content or "").strip()
    if not text:
        raise BulletinLimitExceeded("A bulletin entry cannot be empty.")
    if len(text) > BULLETIN_MAX_ENTRY_CHARS:
        raise BulletinLimitExceeded(f"That entry is {len(text)} characters; the limit is {BULLETIN_MAX_ENTRY_CHARS}.")

    usage = await repo.usage(team_id)
    projected = usage.total_chars - len(entry.content) + len(text)
    if projected > BULLETIN_MAX_TOTAL_CHARS:
        raise BulletinLimitExceeded(
            f"The bulletin would reach {projected} characters (limit {BULLETIN_MAX_TOTAL_CHARS}). Shorten it further."
        )

    await repo.update_content(entry_id, text)
    entry.content = text
    return entry


async def _require_team_owner(request: Request, team_id: str):
    """Resolve the team after checking the caller owns it. Returns (db, team)."""
    user_id = await _user_id_for_request(request)
    db = await get_db_client()
    team = await TeamRepository(db).get_team(team_id)
    if not team:
        raise HTTPException(status_code=404, detail="Team not found")
    if team.owner_user_id != user_id:
        raise HTTPException(status_code=403, detail="Forbidden")
    return db, team


@router.get("/{team_id}/artifacts")
async def list_team_artifacts(team_id: str, request: Request):
    """Artifacts owned by this team, newest first — the team room's panel.

    Not filtered by agent: the panel shows the TEAM's output whoever produced
    it. `agent_id` rides on every row so the UI can attribute each one.
    """
    db, _team = await _require_team_owner(request, team_id)
    return await ArtifactRepository(db).list_by_team(team_id)


async def _team_artifact_turns(db, team_id: str) -> dict[str, list[str]]:
    """Map each turn to the team artifacts it created or updated.

    Powers the chip under a team message: the transcript already carries each
    reply's ``event_id``, so a real key joins the two sides. Timestamps were
    never an option — one turn can register two artifacts, and two agents can
    answer in the same room at once, so proximity would attribute the wrong
    work to the wrong message.
    """
    return await ArtifactHistoryRepository(db).turns_for_team(team_id)


@router.get("/{team_id}/artifact-turns")
async def list_team_artifact_turns(team_id: str, request: Request):
    """Which artifacts each turn in this room produced (event_id → ids)."""
    db, _team = await _require_team_owner(request, team_id)
    return await _team_artifact_turns(db, team_id)


async def _authorize_team_artifact(db, team_id: str, artifact_id: str):
    """Fetch an artifact and enforce it belongs to THIS team.

    The agent-scoped route next to this one requires the caller's agent to BE
    the artifact's agent, which is precisely wrong for a team: the panel shows
    work from several members, and a teammate opening a colleague's artifact is
    the normal case. Team membership of the ARTIFACT replaces that check.

    404 rather than 403 for every refusal, matching the agent route — a
    different status would let a prober map which artifact ids exist. Owning a
    team is also not a way into the owner's private work: a NULL team_id fails
    the same comparison.
    """
    art = await ArtifactRepository(db).get_by_id(artifact_id)
    if art is None or art.team_id != team_id:
        raise HTTPException(404, "artifact not found")
    return art


@router.post("/{team_id}/artifacts/{artifact_id}/view-token")
async def mint_team_view_token(team_id: str, artifact_id: str, request: Request):
    """Mint a short-TTL view token for a team artifact's raw content.

    Authorisation happens HERE, which is where the existing design already put
    it: the token is a bearer capability for exactly one artifact, so the mint
    call is the gate. That is why the payload does not grow a team field — it
    keeps carrying the PRODUCER's agent_id, which is what the raw serving path
    resolves against, and nothing downstream has to learn about teams.

    NOT CURRENTLY CALLED, and that is a decision rather than an oversight
    (Owner, 2026-08-07). The panel renders through ArtifactRenderer, which
    mints on the agent-scoped route using the artifact's own agent_id; that
    route authorises on "does the JWT user own this agent", and every member of
    a team is an agent of the team's single owner, so it already resolves. This
    route states the accurate rule instead — the artifact belongs to THIS team,
    so owning a team is not a way into the owner's private work — and does not
    rest on teams-are-single-owner remaining true. `_authorize_team_artifact`
    is covered by tests/backend/test_team_artifact_view_token.py.
    """
    db, _team = await _require_team_owner(request, team_id)
    art = await _authorize_team_artifact(db, team_id, artifact_id)

    from backend.routes.artifacts import _token as artifact_token

    token = artifact_token.mint(agent_id=art.agent_id, artifact_id=artifact_id)
    return {"token": token, "raw_url": f"/api/public/artifacts/raw/{token}/"}


@router.get("/{team_id}/files")
async def list_team_files(team_id: str, request: Request):
    """Files shared into the team folder, newest first.

    This is the user-facing entry point the folder never had: `_shared/` is a
    sibling of every agent workspace, so the workspace browser cannot see it
    and the only previous way to find a file was an agent naming its path in
    chat.
    """
    db, _team = await _require_team_owner(request, team_id)
    return await _team_files(db, team_id)


@router.post("/{team_id}/members", response_model=TeamOperationResponse)
async def add_member(team_id: str, payload: AddMemberRequest, request: Request):
    user_id = await _user_id_for_request(request)
    db = await get_db_client()
    team_repo = TeamRepository(db)
    member_repo = TeamMemberRepository(db)

    team = await team_repo.get_team(team_id)
    if not team:
        raise HTTPException(status_code=404, detail="Team not found")
    if team.owner_user_id != user_id:
        raise HTTPException(status_code=403, detail="Forbidden")

    agent_row = await db.get_one("agents", {"agent_id": payload.agent_id})
    if not agent_row:
        raise HTTPException(status_code=404, detail="Agent not found")
    if agent_row["created_by"] != user_id:
        raise HTTPException(status_code=403, detail="Cannot add another user's agent")

    added = await member_repo.add_member(team_id, payload.agent_id)
    return TeamOperationResponse(
        success=True,
        message="Agent added to team" if added else "Agent already in team",
    )


@router.delete("/{team_id}/members/{agent_id}", response_model=TeamOperationResponse)
async def remove_member(team_id: str, agent_id: str, request: Request):
    user_id = await _user_id_for_request(request)
    db = await get_db_client()
    team_repo = TeamRepository(db)
    member_repo = TeamMemberRepository(db)

    team = await team_repo.get_team(team_id)
    if not team:
        raise HTTPException(status_code=404, detail="Team not found")
    if team.owner_user_id != user_id:
        raise HTTPException(status_code=403, detail="Forbidden")

    deleted = await member_repo.remove_member(team_id, agent_id)
    if deleted == 0:
        raise HTTPException(status_code=404, detail="Member not found in team")
    return TeamOperationResponse(success=True, message="Member removed")
