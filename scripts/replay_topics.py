"""
replay_topics.py  –  Replay LoCoMo topic-split dialogues through NexusAgent's
Narrative / Memory / SocialNetwork pipeline WITHOUT running the LLM dialogue
(Step 3).

For each turn we:
    Step 1  –  NarrativeService.select()    (Narrative routing / matching)
    Step 4  –  Create Event + update_with_event()   (Narrative persistence)
    Step 5  –  Execute hooks (MemoryModule → EverMemOS, SocialNetworkModule)

Usage:
    # All 10 dialogs, Caroline as agent
    python scripts/replay_topics.py \
        --topics /path/to/locomo_topics.json \
        --locomo /path/to/locomo10.json \
        --perspective caroline

    # Single dialog for testing
    python scripts/replay_topics.py \
        --topics /path/to/locomo_topics.json \
        --locomo /path/to/locomo10.json \
        --perspective melanie --dialog-index 0
"""

from __future__ import annotations

import argparse
import asyncio
import json
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from uuid import uuid4

# ---------------------------------------------------------------------------
# Ensure NexusAgent src is importable
# ---------------------------------------------------------------------------
_SCRIPT_DIR = Path(__file__).resolve().parent
_NEXUS_SRC = _SCRIPT_DIR.parent / "src"
if str(_NEXUS_SRC) not in sys.path:
    sys.path.insert(0, str(_NEXUS_SRC))

from loguru import logger

# -- NexusAgent imports (lazy-ish, but all at top for clarity) --------------
from xyz_agent_context.narrative import (
    Event,
    EventService,
    NarrativeService,
    SessionService,
    TriggerType,
)
from xyz_agent_context.narrative._event_impl.crud import EventCRUD
from xyz_agent_context.schema import (
    HookAfterExecutionParams,
    HookExecutionContext,
    HookIOData,
    HookExecutionTrace,
    WorkingSource,
)
from xyz_agent_context.module import HookManager
from xyz_agent_context.module.memory_module.memory_module import (
    MemoryModule,
    get_memory_module,
)
from xyz_agent_context.module.social_network_module.social_network_module import (
    SocialNetworkModule,
)
from xyz_agent_context.repository import (
    AgentRepository,
    InstanceRepository,
    UserRepository,
)
from xyz_agent_context.schema.instance_schema import (
    InstanceStatus,
    ModuleInstanceRecord,
)
from xyz_agent_context.utils.db_factory import get_db_client
from xyz_agent_context.module import generate_instance_id


# ═══════════════════════════════════════════════════════════════════════════════
# Date helpers
# ═══════════════════════════════════════════════════════════════════════════════

_DATE_RE = re.compile(
    r"(\d{1,2}):(\d{2})\s*(am|pm)\s+on\s+(\d{1,2})\s+(\w+),?\s+(\d{4})",
    re.IGNORECASE,
)

_MONTH_MAP = {
    "january": 1, "february": 2, "march": 3, "april": 4,
    "may": 5, "june": 6, "july": 7, "august": 8,
    "september": 9, "october": 10, "november": 11, "december": 12,
}


def parse_locomo_date(date_str: str) -> Optional[datetime]:
    """Parse '1:56 pm on 8 May, 2023' into a UTC datetime."""
    if not date_str or date_str == "unknown":
        return None
    m = _DATE_RE.search(date_str)
    if not m:
        return None
    hour, minute = int(m.group(1)), int(m.group(2))
    ampm = m.group(3).lower()
    day = int(m.group(4))
    month = _MONTH_MAP.get(m.group(5).lower())
    year = int(m.group(6))
    if month is None:
        return None
    if ampm == "pm" and hour != 12:
        hour += 12
    elif ampm == "am" and hour == 12:
        hour = 0
    return datetime(year, month, day, hour, minute, tzinfo=timezone.utc)


def build_date_lookup(locomo_data: List[Dict]) -> Dict[int, Dict[str, str]]:
    """Build {dialog_index: {dia_id_prefix: session_date_str}} from locomo10."""
    lookup: Dict[int, Dict[str, str]] = {}
    for idx, sample in enumerate(locomo_data):
        conv = sample.get("conversation", {})
        mapping: Dict[str, str] = {}
        session_num = 1
        while True:
            key = f"session_{session_num}"
            date_key = f"{key}_date_time"
            if key not in conv:
                break
            date_str = conv.get(date_key, "unknown")
            for turn in conv[key]:
                mapping[turn["dia_id"]] = date_str
            session_num += 1
        lookup[idx] = mapping
    return lookup


# ═══════════════════════════════════════════════════════════════════════════════
# Turn pairing
# ═══════════════════════════════════════════════════════════════════════════════

def pair_turns_into_rounds(
    cleaned_turns: List[Dict],
    agent_speaker: str,
) -> List[Tuple[str, str, Optional[str]]]:
    """
    Walk through cleaned_turns and pair consecutive user / agent turns into
    (user_input, agent_response, first_dia_id) rounds.

    Consecutive turns by the same role are concatenated with newlines.
    If a topic starts or ends with agent turns only (no user turn), we still
    create a round with an empty counterpart.
    """
    rounds: List[Tuple[str, str, Optional[str]]] = []
    user_buf: List[str] = []
    agent_buf: List[str] = []
    first_dia_id: Optional[str] = None

    def flush():
        nonlocal user_buf, agent_buf, first_dia_id
        if user_buf or agent_buf:
            rounds.append((
                "\n".join(user_buf),
                "\n".join(agent_buf),
                first_dia_id,
            ))
            user_buf = []
            agent_buf = []
            first_dia_id = None

    for turn in cleaned_turns:
        is_agent = turn["speaker"].lower() == agent_speaker.lower()
        dia_id = turn.get("dia_id")

        if is_agent:
            if user_buf and agent_buf:
                flush()
            agent_buf.append(turn["text"])
            if first_dia_id is None:
                first_dia_id = dia_id
        else:
            if agent_buf and user_buf:
                flush()
            user_buf.append(turn["text"])
            if first_dia_id is None:
                first_dia_id = dia_id

    flush()
    return rounds


# ═══════════════════════════════════════════════════════════════════════════════
# ID helpers
# ═══════════════════════════════════════════════════════════════════════════════

def derive_ids(
    dialog_data: Dict, dialog_idx: int, perspective: str,
    agent_id_override: Optional[str] = None,
) -> Tuple[str, str, str, str]:
    """Return (agent_id, user_id, agent_speaker_name, user_speaker_name)."""
    speakers = dialog_data["speakers"]
    sp_a, sp_b = speakers[0], speakers[1]

    if perspective.lower() == sp_a.lower():
        agent_name, user_name = sp_a, sp_b
    elif perspective.lower() == sp_b.lower():
        agent_name, user_name = sp_b, sp_a
    else:
        raise ValueError(
            f"--perspective '{perspective}' does not match speakers "
            f"'{sp_a}' or '{sp_b}' in dialog {dialog_idx}"
        )

    safe_u = re.sub(r"\W+", "_", user_name.lower())
    if agent_id_override:
        agent_id = agent_id_override
    else:
        safe_a = re.sub(r"\W+", "_", agent_name.lower())
        agent_id = f"agent_locomo_d{dialog_idx}_{safe_a}"
    user_id = f"user_locomo_{safe_u}"
    return agent_id, user_id, agent_name, user_name


# ═══════════════════════════════════════════════════════════════════════════════
# DB setup helpers
# ═══════════════════════════════════════════════════════════════════════════════

async def ensure_user(db_client, user_id: str, display_name: str):
    repo = UserRepository(db_client)
    existing = await repo.get_user(user_id)
    if existing:
        return
    await repo.add_user(
        user_id=user_id, user_type="user", display_name=display_name,
    )
    logger.info(f"Created user: {user_id}")


async def ensure_agent(db_client, agent_id: str, agent_name: str, created_by: str):
    repo = AgentRepository(db_client)
    existing = await repo.get_agent(agent_id)
    if existing:
        return
    await repo.add_agent(
        agent_id=agent_id,
        agent_name=agent_name,
        created_by=created_by,
        agent_description=f"LoCoMo replay agent ({agent_name})",
    )
    logger.info(f"Created agent: {agent_id}")


async def ensure_social_instance(db_client, agent_id: str) -> str:
    """Get or create a SocialNetworkModule instance for this agent."""
    inst_repo = InstanceRepository(db_client)
    instances = await inst_repo.get_by_agent(agent_id, module_class="SocialNetworkModule")
    if instances:
        return instances[0].instance_id

    from xyz_agent_context.utils import utc_now
    new_id = generate_instance_id("social")
    record = ModuleInstanceRecord(
        instance_id=new_id,
        module_class="SocialNetworkModule",
        agent_id=agent_id,
        user_id=None,
        is_public=True,
        status=InstanceStatus.ACTIVE,
        description="SocialNetworkModule for LoCoMo replay",
        keywords=["social", "network"],
        topic_hint="Social network interactions",
        created_at=utc_now(),
    )
    await inst_repo.create_instance(record)
    logger.info(f"Created SocialNetworkModule instance: {new_id}")
    return new_id


# ═══════════════════════════════════════════════════════════════════════════════
# Event construction
# ═══════════════════════════════════════════════════════════════════════════════

def make_event(
    agent_id: str,
    user_id: str,
    user_input: str,
    agent_response: str,
    event_time: Optional[datetime],
    narrative_id: Optional[str] = None,
) -> Event:
    now = event_time or datetime.now(timezone.utc)
    return Event(
        id=f"evt_{uuid4().hex[:16]}",
        trigger=TriggerType.CHAT,
        trigger_source=user_id,
        env_context={"input": user_input, "timestamp": now.isoformat()},
        module_instances=[],
        event_log=[],
        final_output=agent_response,
        created_at=now,
        updated_at=now,
        narrative_id=narrative_id,
        agent_id=agent_id,
        user_id=user_id,
    )


# ═══════════════════════════════════════════════════════════════════════════════
# Core replay
# ═══════════════════════════════════════════════════════════════════════════════

async def replay_one_dialog(
    dialog_data: Dict,
    dialog_idx: int,
    perspective: str,
    date_lookup: Dict[str, str],
    inter_turn_delay: float = 0.3,
    agent_id_override: Optional[str] = None,
):
    agent_id, user_id, agent_name, user_name = derive_ids(
        dialog_data, dialog_idx, perspective,
        agent_id_override=agent_id_override,
    )
    logger.info(f"=== Dialog {dialog_idx}: agent={agent_name}({agent_id}), user={user_name}({user_id}) ===")

    db_client = await get_db_client()

    # Ensure user + agent exist in DB
    await ensure_user(db_client, user_id, user_name)
    await ensure_agent(db_client, agent_id, agent_name, user_id)

    # Services
    narrative_service = NarrativeService(agent_id)
    event_service = EventService(agent_id)
    narrative_service.set_event_service(event_service)
    session_service = SessionService()

    event_crud = EventCRUD(agent_id)
    event_crud.set_database_client(db_client)

    session = await session_service.get_or_create_session(user_id, agent_id)

    # Modules
    social_instance_id = await ensure_social_instance(db_client, agent_id)
    memory_module = get_memory_module(agent_id, user_id)
    social_module = SocialNetworkModule(
        agent_id=agent_id,
        user_id=user_id,
        database_client=db_client,
        instance_id=social_instance_id,
    )
    hook_manager = HookManager()
    module_list = [memory_module, social_module]

    topics = dialog_data.get("topics", [])
    total_rounds = 0

    for topic in topics:
        topic_id = topic["topic_id"]
        topic_summary = topic.get("topic_summary", "")
        cleaned = topic.get("cleaned_turns", [])
        if not cleaned:
            continue

        # Force ContinuityDetector to return is_continuous=False at each
        # topic boundary so NarrativeService does embedding retrieval
        # instead of blindly continuing the previous Narrative.
        session.last_query = None
        session.last_response = None
        session.current_narrative_id = None

        rounds = pair_turns_into_rounds(cleaned, agent_name)
        logger.info(
            f"  Topic {topic_id} ({len(rounds)} rounds): {topic_summary[:60]}"
        )

        for ri, (user_input, agent_response, first_dia_id) in enumerate(rounds):
            if not user_input and not agent_response:
                continue

            # Resolve date for this turn
            turn_date_str = date_lookup.get(first_dia_id, "unknown") if first_dia_id else "unknown"
            event_time = parse_locomo_date(turn_date_str)

            # Inject date context on EVERY round so EverMemOS episodes
            # retain explicit temporal anchors for date-related QA.
            if turn_date_str and turn_date_str != "unknown":
                user_input = f"[Conversation date: {turn_date_str}]\n{user_input}"

            # Use non-empty content for Narrative selection
            selection_input = user_input or agent_response

            # -- Step 1: Narrative selection --
            selection = await narrative_service.select(
                agent_id, user_id, selection_input, session=session
            )
            if not selection.narratives:
                logger.warning(f"    Round {ri}: no narratives returned, skipping")
                continue
            narrative = selection.narratives[0]

            # Override Narrative timestamps with real conversation time
            # so that Narrative prompt metadata reflects when the conversation
            # actually happened, not when the replay script ran.
            if event_time:
                if selection.is_new:
                    narrative.created_at = event_time
                narrative.updated_at = event_time

            # -- Create Event --
            event = make_event(
                agent_id, user_id, user_input, agent_response, event_time,
                narrative_id=narrative.id,
            )
            await event_crud.save(event)

            # -- Step 4.4: update Narrative with Event --
            is_default = getattr(narrative, "is_special", None) == "default"
            await narrative_service.update_with_event(
                narrative, event,
                is_main_narrative=not is_default,
                is_default_narrative=is_default,
            )

            # Re-stamp updated_at after NarrativeUpdater overwrites it with now()
            if event_time:
                narrative.updated_at = event_time
                await narrative_service.save_narrative_to_db(narrative)

            # -- Step 5: Execute hooks (Memory + SocialNetwork) --
            hook_params = HookAfterExecutionParams(
                execution_ctx=HookExecutionContext(
                    event_id=event.id,
                    agent_id=agent_id,
                    user_id=user_id,
                    working_source=WorkingSource.CHAT,
                ),
                io_data=HookIOData(
                    input_content=user_input,
                    final_output=agent_response,
                ),
                trace=HookExecutionTrace(),
                event=event,
                narrative=narrative,
            )
            try:
                await hook_manager.hook_after_event_execution(module_list, hook_params)
            except Exception as exc:
                logger.warning(f"    Hook error (round {ri}): {exc}")

            # -- Update session --
            session.last_query = user_input
            session.last_response = agent_response
            session.current_narrative_id = narrative.id
            session.last_query_time = datetime.now(timezone.utc)
            await session_service.save_session(session)

            total_rounds += 1

            if inter_turn_delay > 0:
                await asyncio.sleep(inter_turn_delay)

    logger.success(
        f"=== Dialog {dialog_idx} done: {len(topics)} topics, {total_rounds} rounds ==="
    )
    return total_rounds


# ═══════════════════════════════════════════════════════════════════════════════
# Main
# ═══════════════════════════════════════════════════════════════════════════════

async def async_main(args):
    # Load topic-split data
    with open(args.topics, "r", encoding="utf-8") as f:
        topics_data = json.load(f)
    logger.info(f"Loaded {len(topics_data)} dialogs from {args.topics}")

    # Load original locomo data for date lookup
    with open(args.locomo, "r", encoding="utf-8") as f:
        locomo_data = json.load(f)
    all_date_lookups = build_date_lookup(locomo_data)
    logger.info(f"Built date lookup for {len(all_date_lookups)} dialogs")

    # Determine which dialogs to process
    if args.dialog_index is not None:
        indices = [args.dialog_index]
    else:
        indices = list(range(len(topics_data)))

    t0 = time.time()
    grand_total = 0

    for idx in indices:
        if idx >= len(topics_data):
            logger.warning(f"Dialog index {idx} out of range, skipping")
            continue
        dialog = topics_data[idx]
        date_lookup = all_date_lookups.get(idx, {})
        rounds = await replay_one_dialog(
            dialog_data=dialog,
            dialog_idx=idx,
            perspective=args.perspective,
            date_lookup=date_lookup,
            inter_turn_delay=args.delay,
            agent_id_override=args.agent_id,
        )
        grand_total += rounds

    elapsed = time.time() - t0
    logger.info(
        f"Replay complete: {len(indices)} dialogs, {grand_total} total rounds, "
        f"{elapsed:.1f}s elapsed"
    )


def main():
    parser = argparse.ArgumentParser(
        description="Replay LoCoMo topic-split dialogues through NexusAgent memory pipeline"
    )
    parser.add_argument(
        "--topics", required=True,
        help="Path to topic-split JSON (output of split_by_topic.py)",
    )
    parser.add_argument(
        "--locomo", required=True,
        help="Path to original locomo10.json (for date lookup)",
    )
    parser.add_argument(
        "--perspective", required=True,
        help="Which speaker is the agent (e.g. 'caroline' or 'melanie')",
    )
    parser.add_argument(
        "--dialog-index", type=int, default=None,
        help="Process only this dialog index (0-based). Omit to process all.",
    )
    parser.add_argument(
        "--delay", type=float, default=0.3,
        help="Seconds to wait between turns (default 0.3)",
    )
    parser.add_argument(
        "--agent-id", type=str, default=None,
        help="Override the auto-generated agent_id. If not set, derived from "
             "dialog index + perspective (e.g. agent_locomo_d0_melanie).",
    )

    args = parser.parse_args()
    asyncio.run(async_main(args))


if __name__ == "__main__":
    main()
