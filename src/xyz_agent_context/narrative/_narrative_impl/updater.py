"""
Narrative update implementation

@file_name: updater.py
@author: NetMind.AI
@date: 2025-12-22
@description: Narrative update + LLM dynamic summary generation

Features:
1. update_with_event: Update Narrative with an Event
2. LLM dynamic update: Asynchronously update name, current_summary, actors, topic_keywords
3. build_action_digest: compress this turn's tool actions into the update context
"""

from __future__ import annotations

import asyncio
import json
import re
from datetime import datetime, timezone
from typing import Any, List, Optional, TYPE_CHECKING

from pydantic import BaseModel, Field
from loguru import logger

from ..config import config
from ..models import (
    DynamicSummaryEntry,
    Event,
    EventLogEntry,
    Narrative,
    NarrativeActor,
    NarrativeActorType,
)
from .crud import NarrativeCRUD
from .prompts import NARRATIVE_UPDATE_INSTRUCTIONS
from xyz_agent_context.utils.text import strip_routing_prefix

# Use common utilities from utils

if TYPE_CHECKING:
    from xyz_agent_context.utils.db.database import AsyncDatabaseClient


# ============================================================================
# Action digest (defect A1)
# ============================================================================
# The updater used to read only ``Event.final_output``. Agents increasingly
# deliver their answer through a channel tool (chat / Lark / Slack / bus), which
# degrades final_output to a meta-comment — measured on the full local database:
# 212 events called send_message_to_user_directly, and 83 of them (15.4% of all
# events) had a final_output under 200 characters, 40 of them empty. Everything
# the turn was actually about lived in the event_log and nothing read it, so the
# topic nouns never reached the BM25 retrieval surface and the narrative became
# unreachable on the next turn.
#
# Every constant below is derived from that survey, not guessed:
# reference/self_notebook/data/eventlog_survey_2026-08-12.md
#
# The goal is to get the turn's NOUNS into topic_keywords — not to make the LLM
# restate tool output.

# Total character budget for the rendered block. Survey §5: rendered blocks are
# p50=163 / p95=1238 / p99=2171, so 2000 fits 98.9% of events whole. 1500 was
# the original guess; it would systematically truncate exactly the cohort A1
# exists for (long tool chain + answer displaced into a tool call).
ACTION_DIGEST_BUDGET = 2000

# Per-value caps. Survey §4.1: 88.9% of argument values are <= 120 chars, so the
# generic cap almost never bites. Message bodies get more room because that is
# where a displaced answer lives — in the reference event the nouns "Errno 48"
# and "端口" sit at offsets 555-606 of the delivered body (survey §6.2).
_ARG_VALUE_CAP = 120
_ARG_BODY_CAP = 800

# Defensive ceiling: one pathological argument must not be able to eat the whole
# budget. Measured longest real line is 871 chars (survey §6.3 groundwork).
_MAX_LINE = ACTION_DIGEST_BUDGET // 2

# Keys carrying an outbound message body — the displaced final output.
# Survey §4.6 enumerates which tool uses which key.
_BODY_ARG_KEYS = frozenset({"content", "text", "markdown", "message", "args"})

# Identifier / control keys: 45.9% of all argument instances and zero topic
# value (survey F6). They must go by NAME — agent_id is only 18 characters, so
# no length threshold can filter it.
_DROPPED_ARG_KEYS = frozenset({
    "agent_id", "user_id", "tool_call_id", "max_results", "max_results_per_query",
    "limit", "timeout", "run_in_background", "update_mode", "block",
    "notification_method",
})

# Credential keys. Survey F5 found real Lark app_secrets and Slack / Telegram
# bot tokens sitting in tool_call arguments. Un-redacted they would be written
# into current_summary / topic_keywords, persisted to the narratives row, and
# re-injected into every later system prompt.
_SECRET_KEY_RE = re.compile(
    r"secret|token|password|passwd|api_key|apikey|credential|private_key", re.I
)

# Second layer: a key-name denylist cannot stop a token pasted into a shell
# command line. No occurrence in the local database yet — this guards the shape,
# not a known incident.
_SECRET_VALUE_RE = re.compile(
    r"xox[baprs]-[\w-]{10,}"
    r"|xapp-[\w-]{10,}"
    r"|\d{9,10}:AA[\w-]{30,}"
    r"|sk-[A-Za-z0-9]{20,}"
    r"|gh[pousr]_[A-Za-z0-9]{20,}"
    r"|Bearer\s+[A-Za-z0-9._-]{20,}"
    r"|AKIA[0-9A-Z]{16}"
)

# Path-ish keys are truncated from the HEAD so the tail survives: the topic noun
# of a path is its basename. Head-truncating an 81-char file_path is precisely
# how `deploy.sh` was lost (survey F7).
_PATH_KEY_RE = re.compile(r"path|file|url|uri|dir")

# `mcp__lark_module__lark_send_message` is plumbing; `lark_send_message` is a
# noun. 57 distinct tools carry this prefix.
_MCP_PREFIX_RE = re.compile(r"^mcp__[a-z0-9_]+?_module__")

# A failed action is a different topic state than a successful one ("deploy
# failed" vs "deploy succeeded"), so the status is kept. The output body is not:
# it is 32.4% of all event_log characters and its topic nouns sit at offsets
# 738-7070, unreachable by any sane head slice (survey F3).
_ERROR_OUTPUT_RE = re.compile(
    r'"success"\s*:\s*false|^Error|error:|Traceback|失败', re.I
)

_REDACTED = "<redacted>"

# Secret KEY NAMES in key-value position, checked on the RENDERED TEXT of
# every value. `_SECRET_KEY_RE` alone sees only the TOP-LEVEL argument key,
# so `{"args": {"app_secret": ...}}` sailed through (review round 1, C1) —
# and gating this check on isinstance(dict|list) left the same hole for a
# STRING value carrying `LARK_APP_SECRET=...` under an innocuous key like
# `command` (review round 2, I1). A Lark app_secret is a prefix-less
# alphanumeric string `_SECRET_VALUE_RE` cannot recognise, so the kv shape
# is the only signal. Two properties keep prose safe ("token 用量" must NOT
# be eaten — losing a turn's nouns is the recall gap the digest closes):
# the separator (`:` / `=`) is required, and it must be followed by a
# value-shaped run of at least 8 chars.
_SECRET_KV_RE = re.compile(
    r"[\"']?(?:[\w.-]*(?:secret|token|password|passwd|api_key|apikey|"
    r"credential|private_key)[\w.-]*)[\"']?\s*[:=]\s*[\"']?[\w./+-]{8,}",
    re.I,
)


def _stringify(value: Any) -> str:
    """Render an argument value as text without assuming it is a string."""
    if isinstance(value, str):
        return value
    try:
        return json.dumps(value, ensure_ascii=False)
    except (TypeError, ValueError):
        return str(value)


def _render_argument(key: str, value: Any) -> str:
    """Apply the three-bucket argument policy: redact / cap / tail-keep."""
    text = _stringify(value)

    if (
        _SECRET_KEY_RE.search(key)
        or _SECRET_VALUE_RE.search(text)
        or _SECRET_KV_RE.search(text)
    ):
        return _REDACTED

    if _PATH_KEY_RE.search(key) and len(text) > _ARG_VALUE_CAP:
        # Keep the tail — the basename is the topic noun.
        return "…" + text[-_ARG_VALUE_CAP:]

    cap = _ARG_BODY_CAP if key in _BODY_ARG_KEYS else _ARG_VALUE_CAP
    return text if len(text) <= cap else text[:cap] + "…"


def _render_tool_call(content: dict, outcome: Optional[str]) -> str:
    """Render one tool_call (plus its paired outcome) as a single line."""
    tool_name = _MCP_PREFIX_RE.sub("", content.get("tool_name") or "tool")

    arguments = content.get("arguments")
    rendered = (
        [
            f"{key}={_render_argument(key, value)}"
            for key, value in arguments.items()
            if key not in _DROPPED_ARG_KEYS
        ]
        if isinstance(arguments, dict)
        else []
    )

    line = (
        f"- {tool_name}: " + ", ".join(rendered)
        if rendered
        else f"- {tool_name}"
    )
    if outcome:
        line = f"{line} -> {outcome}"

    if len(line) > _MAX_LINE:
        line = line[:_MAX_LINE] + "…"
    return line


def build_action_digest(event_log: List[EventLogEntry]) -> str:
    """
    Compress one turn's tool actions into a compact, noun-dense block.

    Only ``tool_call`` entries produce output. ``thinking`` is dropped (82.7% of
    entries, and it is process rather than topic identity); ``agent_final_output``
    is dropped (the update context already carries final_output in its own
    section); ``tool_output`` contributes an outcome marker only.

    tool_call and tool_output are paired POSITIONALLY: across all 539 events in
    the survey the two counts always matched and the entries were always strictly
    interleaved, while ``tool_output.tool_call_id`` was populated on only 4 of
    1550 entries — so the id is not usable for pairing.

    Args:
        event_log: The event's step-by-step log.

    Returns:
        The rendered block, or an empty string when the turn ran no tools —
        40.1% of events, which must not get an empty heading.
    """
    lines: List[str] = []
    seen: dict = {}  # line -> index into `lines`, so a repeat can be counted
    repeats: dict = {}  # line -> occurrence count
    pending_index = 0
    outcomes: List[Optional[str]] = []

    # First pass: collect outcomes in tool_output order.
    for entry in event_log:
        if entry.type != "tool_output":
            continue
        content = entry.content if isinstance(entry.content, dict) else {}
        output = content.get("output")
        text = output if isinstance(output, str) else _stringify(output)
        outcomes.append("error" if _ERROR_OUTPUT_RE.search(text[:300]) else None)

    for entry in event_log:
        if entry.type != "tool_call":
            continue
        content = entry.content if isinstance(entry.content, dict) else {}
        outcome = outcomes[pending_index] if pending_index < len(outcomes) else None
        pending_index += 1

        line = _render_tool_call(content, outcome)
        if line in seen:
            # Identical repeats are collapsed but COUNTED: "retried 3 times"
            # and "ran once" are different turn states, and _fit_to_budget
            # already refuses to drop content silently — dedup should not be
            # the one place that does.
            repeats[line] += 1
            continue
        seen[line] = len(lines)
        repeats[line] = 1
        lines.append(line)

    if not lines:
        return ""

    for line, count in repeats.items():
        if count > 1:
            lines[seen[line]] = f"{line} (×{count})"

    return _fit_to_budget(lines)


def _fit_to_budget(lines: List[str]) -> str:
    """
    Trim to ACTION_DIGEST_BUDGET, keeping the most recent steps.

    Truncation is never silent — the surviving block says how many steps were
    dropped, so the LLM (and anyone reading a log) can tell the difference
    between "the agent did three things" and "we only showed you three".
    """
    kept: List[str] = []
    used = 0
    for line in reversed(lines):
        cost = len(line) + 1
        if used + cost > ACTION_DIGEST_BUDGET and kept:
            break
        kept.append(line)
        used += cost
    kept.reverse()

    omitted = len(lines) - len(kept)
    if not omitted:
        return "\n".join(kept)

    marker = f"({omitted} earlier steps omitted)"
    # Make room for the marker rather than letting it push us over budget.
    while kept and used + len(marker) + 1 > ACTION_DIGEST_BUDGET:
        used -= len(kept.pop(0)) + 1
        omitted += 1
        marker = f"({omitted} earlier steps omitted)"

    return "\n".join([marker] + kept)


# ============================================================================
# LLM Output Schema
# ============================================================================

class ActorOutput(BaseModel):
    """Actor output"""
    name: str = Field(description="Actor name")
    actor_type: str = Field(description="Type: user, agent, system, tool")


class NarrativeUpdateOutput(BaseModel):
    """
    LLM 生成的 Narrative 更新内容

    用于随着对话演进动态更新 Narrative 元数据。
    """
    name: str = Field(
        description="Short name for the Narrative (3-8 words), the core topic"
    )
    current_summary: str = Field(
        description=(
            "Structured fact sheet in bullet format. "
            "Format: 'Topic: ...\\nKey facts:\\n- fact1\\n- fact2\\n...\\nStatus: ...' "
            "Max 8-12 bullets. No paragraphs, no filler. Just atomic facts."
        )
    )
    topic_keywords: List[str] = Field(
        default_factory=list,
        description="Concrete topic keywords (5-10 items) for retrieval matching"
    )
    actors: List[ActorOutput] = Field(
        default_factory=list,
        description="Participants: users, Agents, and important named entities mentioned"
    )
    dynamic_summary_entry: str = Field(
        default="",
        description="One short sentence summarizing this turn, e.g. 'User requested X; Agent did Y.'"
    )


class NarrativeUpdater:
    """
    Narrative Updater

    Responsibilities:
    - Update Narrative with Events
    - Regenerate topic hints
    """

    def __init__(self, agent_id: str):
        """
        Initialize updater

        Args:
            agent_id: Agent ID
        """
        self.agent_id = agent_id
        self._crud = NarrativeCRUD(agent_id)
        self._event_service = None  # Dependency injection

    def set_database_client(self, db_client: "AsyncDatabaseClient"):
        """Set the database client"""
        self._crud.set_database_client(db_client)

    def set_event_service(self, event_service):
        """Inject EventService"""
        self._event_service = event_service

    async def update_with_event(
        self,
        narrative: Narrative,
        event: Event,
        is_main_narrative: bool = True,
        is_default_narrative: bool = False
    ) -> Narrative:
        """
        Update Narrative with an Event

        Features:
        - Associate Event ID
        - Update dynamic summary (temporary)
        - Asynchronously trigger LLM update (main_narrative only)

        Args:
            narrative: Narrative object
            event: Event object
            is_main_narrative: Whether this is the main Narrative
                - True: Full update, including async LLM dynamic update
                - False: Basic update only (associate Event, update dynamic_summary)
                  Note: Auxiliary Narrative LLM updates require different prompts,
                  as they provide supplementary information with a different summarization perspective.
                  TODO: Implement dedicated update logic for auxiliary Narratives in the future
            is_default_narrative: Whether this is a default Narrative (is_special="default")
                - True: Only add event_id, no other updates
                - False: Normal update

        Returns:
            Updated Narrative
        """
        logger.debug(f"update_with_event: narrative={narrative.id}, event={event.id}, is_default={is_default_narrative}")

        # [Fix] Reload the latest Narrative from database to avoid overwriting concurrent modifications (e.g., PARTICIPANT)
        # This is because the passed-in narrative object may be a stale version loaded at the start of the flow
        latest_narrative = await self._crud.load_by_id(narrative.id)
        if not latest_narrative:
            logger.warning(f"Narrative {narrative.id} not found in database, skipping update_with_event")
            return narrative

        # Default Narrative: Only add event_id, no other updates
        if is_default_narrative:
            logger.info(f"Default Narrative only adding event_id: {latest_narrative.id}")

            # Add event_id
            if event.id not in latest_narrative.event_ids:
                latest_narrative.event_ids.append(event.id)

            # Update timestamp
            latest_narrative.updated_at = datetime.now(timezone.utc)

            # Save
            await self._crud.save(latest_narrative)

            logger.debug(f"Default Narrative update completed: {latest_narrative.id} (only added event_id)")
            return latest_narrative

        # Non-default Narrative: Normal update flow
        # Add event_id
        if event.id not in latest_narrative.event_ids:
            latest_narrative.event_ids.append(event.id)

        # Temporary dynamic_summary update (waiting for LLM to generate a better version)
        if event.final_output:
            summary_entry = DynamicSummaryEntry(
                event_id=event.id,
                summary=event.final_output[:200],
                timestamp=event.updated_at,
                references=[],
            )
            latest_narrative.dynamic_summary.append(summary_entry)

        # Update timestamp
        latest_narrative.updated_at = datetime.now(timezone.utc)

        # Save basic updates
        await self._crud.save(latest_narrative)

        # EverMemOS write has been migrated to MemoryModule.hook_after_event_execution()
        # See docs/MEMORY_MODULE_REFACTOR.md

        # Update the passed-in object reference so subsequent code uses the latest data
        narrative = latest_narrative

        # Determine whether to trigger LLM update (async execution, non-blocking)
        # Note: Only main_narrative triggers the async LLM update
        # Auxiliary Narratives only get basic updates for now; dedicated update logic can be implemented in the future
        if is_main_narrative:
            event_count = len(narrative.event_ids)
            update_interval = config.NARRATIVE_LLM_UPDATE_INTERVAL

            if update_interval > 0 and event_count % update_interval == 0:
                logger.info(f"Triggering Narrative LLM update: {narrative.id} (event_count={event_count})")
                # Async execution, non-blocking main flow. Tracked via `spawn`
                # (incident lesson #2) — this is the same detached path whose
                # 401s went unnoticed for ~2 weeks in 2026-07; the credential
                # alerting added then only fires if the task actually runs and
                # its failure actually surfaces.
                from xyz_agent_context.utils.background_tasks import spawn

                spawn(
                    self._async_llm_update(narrative, event),
                    name=f"narrative_llm_update:{narrative.id}",
                )
        else:
            # Auxiliary Narrative: Only record basic info, skip LLM update
            # TODO: Implement dedicated update logic for auxiliary Narratives in the future
            # Auxiliary Narratives have a different summarization perspective than main_narrative, requiring different prompts
            logger.debug(f"Skipping LLM update for auxiliary Narrative: {narrative.id}")

        return narrative

    # _async_evermemos_write has been migrated to MemoryModule.hook_after_event_execution()
    # See docs/MEMORY_MODULE_REFACTOR.md

    async def _async_llm_update(
        self,
        narrative: Narrative,
        event: Event,
    ) -> None:
        """
        Asynchronously update Narrative metadata using LLM

        Updated content:
        - narrative_info.name
        - narrative_info.current_summary
        - narrative_info.actors
        - topic_keywords
        - dynamic_summary (last entry)

        Args:
            narrative: Narrative object
            event: Latest Event object
        """
        from xyz_agent_context.agent_framework.llm.failure import is_credential_error
        from xyz_agent_context.agent_framework.providers.resolver import (
            ProviderResolverError,
            inject_owner_helper_credentials,
        )
        from xyz_agent_context.services.background_llm_alerts import (
            alert_background_llm_failure,
        )
        from xyz_agent_context.utils.db.db_factory import get_db_client

        # This runs in a detached ``asyncio.create_task`` (see caller) whose
        # context does NOT carry the per-turn helper-LLM config that
        # AgentRuntime.run set. Resolve the agent OWNER's Helper LLM here so the
        # update runs on the user's provider — never the platform key it used to
        # silently fall through to (2026-07 credential incident).
        owner_user_id = None
        try:
            db = await get_db_client()
            owner_user_id = await inject_owner_helper_credentials(
                narrative.agent_id, db
            )
        except ProviderResolverError as e:
            # No usable provider / free tier exhausted. Do NOT fall through to
            # the platform key — skip the update and surface it to the owner.
            await alert_background_llm_failure(
                agent_id=narrative.agent_id,
                owner_user_id=None,
                source="narrative_update",
                error=e,
                source_id=narrative.id,
            )
            return
        except Exception as e:
            logger.exception(
                f"Narrative update credential injection failed: {narrative.id}, error={e}"
            )
            return

        try:
            logger.info(f"Starting LLM update for Narrative: {narrative.id}")

            # Build context: recent conversation history
            context = await self._build_update_context(narrative, event)

            # Call LLM to generate update content
            update_output = await self._call_llm_for_update(narrative, context)

            if update_output:
                # Apply updates
                await self._apply_llm_update(narrative, update_output, event)
                logger.info(f"LLM Narrative update completed: {narrative.id}")
            else:
                logger.warning(f"LLM update failed, skipping: {narrative.id}")

        except Exception as e:
            # A credential-class failure here means the resolved key is bad
            # (expired/revoked) — alert the owner instead of silently degrading
            # long memory. Transient failures stay log-only (retried next turn).
            if is_credential_error(e):
                await alert_background_llm_failure(
                    agent_id=narrative.agent_id,
                    owner_user_id=owner_user_id,
                    source="narrative_update",
                    error=e,
                    source_id=narrative.id,
                )
            logger.exception(f"LLM Narrative update exception: {narrative.id}, error={e}")

    async def _build_update_context(self, narrative: Narrative, event: Event) -> str:
        """Build context for LLM update"""
        context_parts = []

        # Current Narrative information
        context_parts.append("## Current Narrative Information")
        context_parts.append(f"- Name: {narrative.narrative_info.name}")
        # Read-side cap: 291 prod rows carry >1,500-char frozen descriptions
        # (max 198,398) that the write-side cap never backfills. Here the
        # description is the object BEING updated, so clamping loses nothing.
        context_parts.append(
            "- Description: "
            f"{narrative.narrative_info.description[:config.DESCRIPTION_MAX_LENGTH]}"
        )
        context_parts.append(f"- Current Summary: {narrative.narrative_info.current_summary}")
        context_parts.append(f"- Keywords: {', '.join(narrative.topic_keywords or [])}")
        context_parts.append("")

        # Recent conversation history
        context_parts.append("## Recent Conversation History")

        # Get recent summaries from dynamic_summary
        recent_count = config.NARRATIVE_LLM_UPDATE_EVENTS_COUNT
        recent_summaries = narrative.dynamic_summary[-recent_count:]
        for i, entry in enumerate(recent_summaries):
            context_parts.append(f"{i+1}. {entry.summary}")

        context_parts.append("")

        # Latest Event details
        context_parts.append("## Latest Conversation")
        if event.env_context:
            user_input = event.env_context.get("input", "")
            if user_input:
                context_parts.append(f"User Input: {user_input}")
        if event.final_output:
            context_parts.append(f"Agent Response: {event.final_output[:500]}")

        # What the agent actually DID this turn (defect A1). final_output is the
        # agent's self-report; when the answer was delivered through a channel
        # tool it degrades to "already sent" and carries none of the turn's
        # nouns. This section is what makes those nouns reachable by BM25 next
        # turn. Omitted entirely when no tool ran — no empty heading.
        action_digest = build_action_digest(event.event_log)
        if action_digest:
            context_parts.append("")
            context_parts.append("## Actions taken this turn")
            context_parts.append(action_digest)

        return "\n".join(context_parts)

    async def _call_llm_for_update(
        self,
        narrative: Narrative,
        context: str
    ) -> Optional[NarrativeUpdateOutput]:
        """Call LLM to generate Narrative update content.

        Exceptions propagate to ``_async_llm_update``, which classifies them:
        a credential-class failure raises an owner alert (it used to be
        swallowed here as ``return None``, which is exactly how an expired key
        went unnoticed for two weeks). The caller treats a ``None`` return as
        "LLM produced nothing" and skips the update quietly.
        """
        from xyz_agent_context.agent_framework.llm.helper_sdk import get_helper_sdk

        instructions = NARRATIVE_UPDATE_INSTRUCTIONS

        from xyz_agent_context.narrative.config import config as narrative_config
        sdk = get_helper_sdk()
        result = await sdk.llm_function(
            instructions=instructions,
            user_input=context,
            output_type=NarrativeUpdateOutput,
            model=narrative_config.NARRATIVE_LLM_UPDATE_MODEL,
            reasoning_effort=narrative_config.NARRATIVE_LLM_UPDATE_REASONING_EFFORT or None,
        )

        return result.final_output

    async def _apply_llm_update(
        self,
        narrative: Narrative,
        update_output: NarrativeUpdateOutput,
        event: Event
    ) -> None:
        """
        Apply LLM-generated updates

        [Important] To avoid lost update issues, reload the latest Narrative from database first,
        then only update LLM-generated fields, preserving the latest actors and active_instances from the database.
        This is because during async execution, other processes may have already modified actors (e.g., adding PARTICIPANT).
        """
        # [Fix] Reload the latest Narrative from database to avoid overwriting other concurrent modifications
        latest_narrative = await self._crud.load_by_id(narrative.id)
        if not latest_narrative:
            logger.warning(f"Narrative {narrative.id} not found in database, skipping LLM update")
            return

        # Update narrative_info (only update name and current_summary, preserve actors).
        # The helper LLM is handed raw event text and copies it into the name,
        # channel label included — prod carries lines called
        # "[From Liam] * 👊 刚甩过去..." and "[From U082541Q6AX] stop gre...".
        # Once the label is in the name it is in the retrieval surface, and the
        # next message from that channel matches its own thread on the sender
        # at a low in-pool df (audit 1492: margin 357.79, judge never ran).
        # Strip it on the way in; keep the raw name if that is all there was.
        latest_narrative.narrative_info.name = (
            strip_routing_prefix(update_output.name).strip() or update_output.name
        )
        latest_narrative.narrative_info.current_summary = update_output.current_summary
        # Note: Do not update actors, preserve the latest actors from database (including PARTICIPANT)

        # Update topic_keywords
        latest_narrative.topic_keywords = update_output.topic_keywords

        # Update the last dynamic_summary entry
        if latest_narrative.dynamic_summary and update_output.dynamic_summary_entry:
            latest_narrative.dynamic_summary[-1].summary = update_output.dynamic_summary_entry

        # Update timestamp
        latest_narrative.updated_at = datetime.now(timezone.utc)

        # Save to database
        await self._crud.save(latest_narrative)

        logger.debug(
            f"LLM update applied: name={update_output.name}, "
            f"keywords={update_output.topic_keywords}"
        )

    # Embedding-update machinery removed (unified-memory refactor, 2026-06-04):
    # narrative routing is BM25 over name/summary/keywords, so there is no
    # routing_embedding / topic_hint / VectorStore to maintain. The DB columns
    # (routing_embedding, embedding_updated_at, events_since_last_embedding_update,
    # topic_hint) are left as inert tombstones per binding rule #6 (no
    # destructive migrations); nothing reads or writes them anymore.
