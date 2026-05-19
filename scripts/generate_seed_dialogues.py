#!/usr/bin/env python3
"""
Generate high-quality seed dialogues for module-focused evaluation.

This first version only generates conversation data plus lightweight metadata.
It does not run replay, invoke module tools, or perform any write-back.

The script exposes a single `--turns` argument for the total dialogue length.
Internally it splits generation into fixed chunks of up to 10 turns:
    34 turns -> 10 + 10 + 10 + 4

Each model call for dialogue generation returns only the newly appended turns.
The script manually merges those turns and incrementally updates the stored JSON.
Coverage extraction, QA generation, and quality review happen once after the
full conversation has been assembled.

Examples:
    python scripts/generate_seed_dialogues.py
    python scripts/generate_seed_dialogues.py --preset art_memory
    python scripts/generate_seed_dialogues.py --preset art_memory --turns 34
    python scripts/generate_seed_dialogues.py --preset identity_network --modules memory,social_network
    python scripts/generate_seed_dialogues.py --preset identity_network --qa-seed "Ask about canonical social entities and relationships."
    python scripts/generate_seed_dialogues.py --seed-catalog --case-id sn_composite_query_01 --turns 30
"""

from __future__ import annotations

import argparse
import asyncio
import json
import re
import sys
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from openai import AsyncOpenAI
from pydantic import BaseModel, Field, ValidationError

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT / "src"))


DEFAULT_MODEL = "gpt-5.4"
DEFAULT_OUTPUT_DIR = _ROOT / "benchmark" / "generated_seed_data"
DEFAULT_SEED_CATALOG = DEFAULT_OUTPUT_DIR / "social_network_issue_seed_catalog.json"
DEFAULT_TURNS = 10
TURN_CHUNK_SIZE = 10

SUPPORTED_TARGET_MODULES = ("memory", "social_network", "awareness", "job")
DEFERRED_MODULES = {
    "rag": "RAG is deferred in v1 because this generator is conversation-only and RAG ingestion is a separate stage.",
    "matrix": "Matrix is excluded in v1 because it depends on Matrix registration, rooms, and external channel operations.",
    "skill": "Skill is excluded in v1 because it depends on real skill files, config persistence, and study lifecycle actions.",
}
DEFAULT_QA_MODULE_DESCRIPTIONS = {
    "memory": (
        "Memory QA should ask about stable user facts, time anchors, long-term preferences, "
        "past experiences, and future follow-up hooks that can be answered from the dialogue."
    ),
    "social_network": (
        "Social-network QA should ask about named entities, canonical identities, aliases, "
        "relationships, roles, organizations, affiliations, contact-like details, and entity-specific background."
    ),
    "awareness": (
        "Awareness QA should ask about explicit collaboration preferences and whether the agent adapted "
        "to the user's requested communication style or working mode in later turns."
    ),
    "job": (
        "Job QA should ask about background tasks the user asked the agent to schedule: their type "
        "(one-off / scheduled / ongoing), trigger timing (specific date, cron cadence, interval, or "
        "end_condition), payload intent, target entity (self vs. other), and dependencies between jobs."
    ),
}
FORBIDDEN_MARKERS = (
    "send_message_to_user_directly",
    "tool call",
    "trace.md",
    "agent loop",
    "mcp ",
    "mcp_",
    "rag_query(",
    "job_create(",
    "matrix_send_message(",
    "skill_save_config(",
)
CJK_RE = re.compile(r"[\u3400-\u9fff]")


@dataclass(frozen=True)
class SeedPreset:
    name: str
    dialogue_seed: str
    recommended_modules: tuple[str, ...]
    tone_hint: str = ""


@dataclass(frozen=True)
class GenerationInput:
    preset: SeedPreset
    qa_seed: str | None = None
    qa_seed_source: str | None = None
    source_metadata: dict[str, Any] | None = None


PRESETS: dict[str, SeedPreset] = {
    "art_memory": SeedPreset(
        name="art_memory",
        dialogue_seed="Two people have an in-depth discussion about Van Gogh's oil paintings and their influence on modern art, gradually revealing stable personal background details, long-term interests, and future plans.",
        recommended_modules=("memory",),
        tone_hint="Thoughtful and aesthetically sensitive, with a slight autobiographical feel.",
    ),
    "identity_network": SeedPreset(
        name="identity_network",
        dialogue_seed="A user asks an agent about collaboration opportunities and, during the conversation, introduces themselves, their teammates, and partner organizations, while also mentioning some identity and contact details.",
        recommended_modules=("social_network",),
        tone_hint="Professional and information-dense, but still natural.",
    ),
    "preference_alignment": SeedPreset(
        name="preference_alignment",
        dialogue_seed="A user wants the agent to better match their pace and style in future collaboration, so they clearly express several communication preferences and working-style expectations.",
        recommended_modules=("awareness",),
        tone_hint="Like a real alignment conversation, not a requirement checklist.",
    ),
    "career_background": SeedPreset(
        name="career_background",
        dialogue_seed="While discussing a career transition, the user shares their background and important time points, mentions several key colleagues and organizations, and also reveals long-term work preferences they care about.",
        recommended_modules=("memory", "social_network", "awareness"),
        tone_hint="Balanced between personal story and professional context, with clear structure.",
    ),
    "project_followup": SeedPreset(
        name="project_followup",
        dialogue_seed="A user follows up with an agent on an ongoing project, reviews prior context, updates information about relevant team members, and restates how they want the agent to report progress and collaborate going forward.",
        recommended_modules=("memory", "social_network", "awareness"),
        tone_hint="Like a real project follow-up conversation: specific, restrained, and continuous.",
    ),
    # --------------------------------------------------------------------
    # Job presets (Type A — creation testing)
    # --------------------------------------------------------------------
    "job_multi_one_off": SeedPreset(
        name="job_multi_one_off",
        dialogue_seed=(
            "A busy professional asks the agent to set up several one-off reminders at distinct future dates "
            "(e.g. a board deck send-off in a few months, a certificate renewal later in the year, a personal "
            "anniversary call on a specific date). Each reminder has its own date, time, and concrete payload, "
            "and the user expects the agent to schedule them as separate one-time background tasks."
        ),
        recommended_modules=("job",),
        tone_hint="Brisk and organized — like someone running through a checklist with their assistant.",
    ),
    "job_mixed_scheduled": SeedPreset(
        name="job_mixed_scheduled",
        dialogue_seed=(
            "A user sets up multiple recurring routines that mix two natural scheduling patterns: "
            "fixed-clock-time recurring tasks (e.g. 'every weekday at 9:30am', 'every Friday at 5pm') AND "
            "interval-based recurring tasks (e.g. 'every 4 hours', 'every other day'). The agent should "
            "create scheduled jobs and pick the right trigger form for each phrasing."
        ),
        recommended_modules=("job",),
        tone_hint="Practical operator setting up a daily/weekly rhythm — concise and instruction-oriented.",
    ),
    "job_ongoing_sales": SeedPreset(
        name="job_ongoing_sales",
        dialogue_seed=(
            "A sales manager assigns ongoing follow-up tasks for several distinct prospects in a single conversation. "
            "Each prospect has a name and id, a specific business context, an interaction cadence (daily, every few days), "
            "and a clear end_condition tied to whether the deal closes or is declined. The user also asks for a daily "
            "status report to themselves at a fixed time. The agent must distinguish ongoing customer follow-ups from "
            "the scheduled status report, and route the target identity correctly (customer vs. manager)."
        ),
        recommended_modules=("job",),
        tone_hint="Manager voice — directive but specific, mixes customer details with operational rhythm.",
    ),
    "job_all_types_mixed": SeedPreset(
        name="job_all_types_mixed",
        dialogue_seed=(
            "A user runs through a single coherent scenario (e.g. a hire, a launch, a project cutover) that requires "
            "ALL THREE job types in one conversation: an ongoing task with a clear end_condition, a one-off reminder "
            "tied to a specific date, AND a recurring scheduled summary on a weekly cadence. The agent must hold the "
            "three intents straight and create one job per type — no mixing up scheduled with ongoing, etc."
        ),
        recommended_modules=("job",),
        tone_hint="A planner thinking out loud — natural transitions between the three intents.",
    ),
    "job_negative_no_jobs": SeedPreset(
        name="job_negative_no_jobs",
        dialogue_seed=(
            "A user asks the agent for a series of immediate, in-conversation tasks (translation, drafting, summarizing, "
            "research-right-now). The conversation deliberately includes phrases that LOOK schedulable — words like "
            "'always', 'schedule', 'remind', 'from now on' — but in context every one is either an immediate one-shot "
            "request, a long-term style preference, or a definition lookup. The agent must refrain from creating any "
            "background job; over-triggering is the failure mode."
        ),
        recommended_modules=("job",),
        tone_hint="Quick-fire, no-nonsense — someone burning through a to-do list, not delegating.",
    ),
    "job_dependent_jobs": SeedPreset(
        name="job_dependent_jobs",
        dialogue_seed=(
            "A user describes a multi-step workflow where each step explicitly waits for the previous one to complete "
            "(e.g. research → draft → review meeting). The agent must model this as a chain of one-off jobs with "
            "dependencies wired through depends_on_job_ids — not as three independent reminders. The same conversation "
            "also includes one INDEPENDENT recurring scheduled job that should NOT be wired into the chain."
        ),
        recommended_modules=("job",),
        tone_hint="Deliberate workflow planner — names each step explicitly with 'first', 'once that's done', 'after that'.",
    ),
}


class DialogueRound(BaseModel):
    turn_index: int
    user_message: str
    agent_reply: str


class MemoryCoverage(BaseModel):
    facts: list[str] = Field(default_factory=list)
    time_anchors: list[str] = Field(default_factory=list)
    followup_hooks: list[str] = Field(default_factory=list)


class SocialNetworkCoverage(BaseModel):
    entities: list[str] = Field(default_factory=list)
    relations: list[str] = Field(default_factory=list)
    contact_or_org_details: list[str] = Field(default_factory=list)


class AwarenessCoverage(BaseModel):
    explicit_preferences: list[str] = Field(default_factory=list)
    agent_adaptations: list[str] = Field(default_factory=list)


class ModuleCoverage(BaseModel):
    memory: MemoryCoverage | None = None
    social_network: SocialNetworkCoverage | None = None
    awareness: AwarenessCoverage | None = None


class QAItem(BaseModel):
    question: str
    tester_hint_answer: str
    target_modules: list[Literal["memory", "social_network", "awareness"]]
    evidence_turns: list[int]


class SeedCatalogItem(BaseModel):
    case_id: str
    issue: str
    recommended_modules: list[Literal["memory", "social_network", "awareness"]]
    dialogue_seed: str
    qa_seed: str
    required_qa_phrases: list[str] = Field(default_factory=list)
    stress_signal: str = ""


class SeedCatalog(BaseModel):
    catalog_name: str = "seed_catalog"
    version: int | None = None
    default_modules: list[Literal["memory", "social_network", "awareness"]] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)
    items: list[SeedCatalogItem]


class DialogueChunkContent(BaseModel):
    new_dialogue_rounds: list[DialogueRound]


class MetadataContent(BaseModel):
    module_coverage: ModuleCoverage
    qa_items: list[QAItem]


class GeneratedContent(BaseModel):
    dialogue_rounds: list[DialogueRound]
    module_coverage: ModuleCoverage
    qa_items: list[QAItem]


class SelfReviewResult(BaseModel):
    passed: bool
    issues: list[str] = Field(default_factory=list)
    revision_guidance: list[str] = Field(default_factory=list)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Generate high-quality seed dialogues for module-focused evaluation.",
    )
    parser.add_argument(
        "--preset",
        help=(
            "Preset name(s) to generate. Use a single preset name, a comma-separated list, "
            "or omit this flag to generate all presets."
        ),
    )
    parser.add_argument(
        "--seed-catalog",
        nargs="?",
        const=str(DEFAULT_SEED_CATALOG),
        help=(
            "Load generation cases from a seed catalog JSON. If used without a path, "
            f"defaults to {DEFAULT_SEED_CATALOG}."
        ),
    )
    parser.add_argument(
        "--case-id",
        help=(
            "Catalog case_id(s) to generate. Use a single case_id, a comma-separated list, "
            "or omit this flag with --seed-catalog to generate all catalog cases."
        ),
    )
    parser.add_argument(
        "--modules",
        help="Comma-separated target modules. Supported: memory,social_network,awareness,job",
    )
    parser.add_argument(
        "--out",
        help=(
            "Output file or directory. For a single sample, this may be a .json file or a directory. "
            "For multiple samples, it must be a directory."
        ),
    )
    parser.add_argument(
        "--model",
        default=DEFAULT_MODEL,
        help=f"OpenAI model name. Defaults to {DEFAULT_MODEL}.",
    )
    parser.add_argument(
        "--turns",
        type=int,
        default=DEFAULT_TURNS,
        help=(
            f"Total desired dialogue turns. The script internally splits generation into chunks of up to {TURN_CHUNK_SIZE} turns."
        ),
    )
    qa_seed_group = parser.add_mutually_exclusive_group()
    qa_seed_group.add_argument(
        "--qa-seed",
        help=(
            "Custom QA seed/evaluation focus. If provided, the script will not insert the default "
            "module-specific QA prompt descriptions."
        ),
    )
    qa_seed_group.add_argument(
        "--qa-seed-file",
        help=(
            "Path to a UTF-8 text file containing the custom QA seed. Mutually exclusive with --qa-seed."
        ),
    )
    return parser.parse_args()


def parse_modules(raw: str | None) -> list[str] | None:
    if raw is None:
        return None

    modules = []
    for item in raw.split(","):
        name = item.strip()
        if not name or name in modules:
            continue
        modules.append(name)

    if not modules:
        raise ValueError("`--modules` was provided but no module names were parsed.")

    unsupported = [module for module in modules if module not in SUPPORTED_TARGET_MODULES]
    if unsupported:
        reasons = []
        for module in unsupported:
            if module in DEFERRED_MODULES:
                reasons.append(f"{module}: {DEFERRED_MODULES[module]}")
            else:
                reasons.append(f"{module}: unsupported in v1")
        raise ValueError(
            "Unsupported modules requested.\n"
            + "\n".join(f"- {line}" for line in reasons)
            + f"\nSupported modules in v1: {', '.join(SUPPORTED_TARGET_MODULES)}"
        )

    return modules


def parse_presets(raw: str | None) -> list[SeedPreset]:
    if raw is None:
        return [PRESETS[name] for name in sorted(PRESETS.keys())]

    names: list[str] = []
    for item in raw.split(","):
        name = item.strip()
        if not name or name in names:
            continue
        names.append(name)

    if not names:
        raise ValueError("`--preset` was provided but no preset names were parsed.")

    invalid = [name for name in names if name not in PRESETS]
    if invalid:
        raise ValueError(
            "Unknown preset name(s): "
            + ", ".join(invalid)
            + f"\nAvailable presets: {', '.join(sorted(PRESETS.keys()))}"
        )

    return [PRESETS[name] for name in names]


def parse_case_ids(raw: str | None) -> list[str] | None:
    if raw is None:
        return None

    case_ids: list[str] = []
    for item in raw.split(","):
        case_id = item.strip()
        if not case_id or case_id in case_ids:
            continue
        case_ids.append(case_id)

    if not case_ids:
        raise ValueError("`--case-id` was provided but no case IDs were parsed.")

    return case_ids


def load_seed_catalog_inputs(catalog_arg: str, case_id_arg: str | None) -> list[GenerationInput]:
    catalog_path = Path(catalog_arg)
    try:
        raw_json = catalog_path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ValueError(f"Failed to read `--seed-catalog` {catalog_path}: {exc}") from exc

    try:
        catalog = SeedCatalog.model_validate_json(raw_json)
    except ValidationError as exc:
        raise ValueError(f"Invalid seed catalog JSON `{catalog_path}`: {exc}") from exc

    if not catalog.items:
        raise ValueError(f"Seed catalog contains no items: {catalog_path}")

    duplicate_ids = {
        item.case_id
        for item in catalog.items
        if sum(1 for other in catalog.items if other.case_id == item.case_id) > 1
    }
    if duplicate_ids:
        raise ValueError(f"Seed catalog has duplicate case_id values: {', '.join(sorted(duplicate_ids))}")

    selected_case_ids = parse_case_ids(case_id_arg)
    if selected_case_ids is None:
        selected_items = catalog.items
    else:
        by_id = {item.case_id: item for item in catalog.items}
        missing = [case_id for case_id in selected_case_ids if case_id not in by_id]
        if missing:
            raise ValueError(
                "Unknown catalog case_id(s): "
                + ", ".join(missing)
                + "\nAvailable case IDs: "
                + ", ".join(item.case_id for item in catalog.items)
            )
        selected_items = [by_id[case_id] for case_id in selected_case_ids]

    inputs: list[GenerationInput] = []
    for item in selected_items:
        recommended_modules = item.recommended_modules or catalog.default_modules
        if not recommended_modules:
            raise ValueError(f"Catalog item `{item.case_id}` has no recommended_modules.")

        # Reuse the normal module parser so catalog validation and CLI validation stay aligned.
        parsed_modules = parse_modules(",".join(recommended_modules))
        if parsed_modules is None:
            raise ValueError(f"Catalog item `{item.case_id}` has no valid recommended modules.")

        preset = SeedPreset(
            name=item.case_id,
            dialogue_seed=item.dialogue_seed,
            recommended_modules=tuple(parsed_modules),
            tone_hint=(
                "Natural and user-facing. Create realistic ambiguity through ordinary wording, "
                "but do not mention test internals or implementation mechanics."
            ),
        )
        inputs.append(
            GenerationInput(
                preset=preset,
                qa_seed=item.qa_seed,
                qa_seed_source="catalog",
                source_metadata={
                    "source": "seed_catalog",
                    "catalog_path": str(catalog_path),
                    "catalog_name": catalog.catalog_name,
                    "catalog_version": catalog.version,
                    "case_id": item.case_id,
                    "issue": item.issue,
                    "required_qa_phrases": item.required_qa_phrases,
                    "stress_signal": item.stress_signal,
                },
            )
        )

    return inputs


def load_custom_qa_seed(args: argparse.Namespace) -> str | None:
    if args.qa_seed is not None:
        qa_seed = args.qa_seed.strip()
        if not qa_seed:
            raise ValueError("`--qa-seed` was provided but it is empty.")
        return qa_seed

    if args.qa_seed_file is not None:
        qa_seed_path = Path(args.qa_seed_file)
        try:
            qa_seed = qa_seed_path.read_text(encoding="utf-8").strip()
        except OSError as exc:
            raise ValueError(f"Failed to read `--qa-seed-file` {qa_seed_path}: {exc}") from exc
        if not qa_seed:
            raise ValueError(f"`--qa-seed-file` is empty: {qa_seed_path}")
        return qa_seed

    return None


def build_default_qa_seed(target_modules: list[str]) -> str:
    module_descriptions = [
        f"- {module}: {DEFAULT_QA_MODULE_DESCRIPTIONS[module]}"
        for module in target_modules
    ]
    return (
        "Generate exactly 5 evaluation questions that are grounded only in the completed dialogue. "
        "The questions should test whether later replay/QA can retrieve and reason over the information "
        "that the selected modules are expected to preserve.\n\n"
        "Module-specific QA focus:\n"
        + "\n".join(module_descriptions)
        + "\n\n"
        "Prefer questions that require connecting evidence across turns instead of copying one sentence. "
        "Every question must include evidence_turns pointing to the turns that support the answer."
    )


def resolve_qa_seed(
    *,
    custom_qa_seed: str | None,
    target_modules: list[str],
) -> tuple[str, str]:
    if custom_qa_seed is not None:
        return custom_qa_seed, "custom"
    return build_default_qa_seed(target_modules), "default"


def build_client() -> AsyncOpenAI:
    try:
        from xyz_agent_context.agent_framework.api_config import openai_config
    except Exception as exc:
        raise RuntimeError(
            "Failed to load the project OpenAI helper config. "
            "Please run this script inside the project environment where repo dependencies are installed.\n"
            f"Original error: {exc}"
        ) from exc

    if not openai_config.api_key:
        raise RuntimeError(
            "No OpenAI API key found in current helper LLM config. "
            "Please configure the helper_llm slot or legacy OPENAI key first."
        )

    kwargs: dict[str, Any] = {"api_key": openai_config.api_key}
    if openai_config.base_url:
        kwargs["base_url"] = openai_config.base_url
    return AsyncOpenAI(**kwargs)


def build_module_constraint_text(target_modules: list[str]) -> str:
    sections: list[str] = []
    for module in target_modules:
        if module == "memory":
            sections.append(
                "## memory constraints\n"
                "- Across the full conversation, include at least 3 stable facts that can still be asked about later.\n"
                "- Include at least 1 clear time anchor such as a year, month, date, or stage in life.\n"
                "- Include at least 1 long-term preference, experience, or future plan that creates a follow-up hook."
            )
        elif module == "social_network":
            sections.append(
                "## social_network constraints\n"
                "- Across the full conversation, include at least 2 named entities besides the agent framing.\n"
                "- Include at least 1 explicit relationship between people or between a person and an organization.\n"
                "- Include at least 1 organization, role, contact detail, or affiliation detail that could later be extracted."
            )
        elif module == "awareness":
            sections.append(
                "## awareness constraints\n"
                "- Across the full conversation, include at least 2 explicit collaboration or communication preferences from the user.\n"
                "- The agent must adapt in later turns to those preferences rather than only acknowledging them once.\n"
                "- The preferences should sound natural and conversational, not like a synthetic checklist."
            )
        elif module == "job":
            sections.append(
                "## job constraints\n"
                "- Across the full conversation, the user must request at least 2 distinct background tasks that span "
                "more than one job_type out of {one_off, scheduled, ongoing}.\n"
                "- Use natural conversational phrasing for timing — e.g. 'remind me on March 15 at 3pm', 'every weekday "
                "at 9am', 'every 4 hours', 'until the deal closes'. Do NOT mention internal field names like 'cron', "
                "'interval_seconds', 'trigger_config', or 'job_create' — those belong to the system, not the user.\n"
                "- Include at least one negative trap: a sentence that LOOKS schedulable (uses 'always', 'schedule', "
                "'remind') but is actually an immediate one-shot request, an awareness preference, or a definition lookup. "
                "The agent should NOT create a job for the trap.\n"
                "- The user must declare a timezone or location early in the conversation so the agent can resolve "
                "absolute times.\n"
                "- If applicable, include one ongoing task aimed at a third party (e.g. a customer or candidate). "
                "Mention that person's name or user_id explicitly so the agent can record them as the target entity."
            )
    return "\n".join(sections)


def prompt_seed_label(preset: SeedPreset) -> str:
    """Avoid leaking catalog issue labels such as `sn_keyword_*` into LLM prompts."""
    if preset.name.startswith("sn_"):
        return "catalog_case"
    return preset.name


def build_chunk_prompt(
    preset: SeedPreset,
    target_modules: list[str],
    *,
    existing_rounds: list[DialogueRound],
    chunk_turn_count: int,
) -> tuple[str, str]:
    start_turn = len(existing_rounds) + 1
    end_turn = len(existing_rounds) + chunk_turn_count
    module_constraints = build_module_constraint_text(target_modules)
    mode_instruction = (
        "Start a new conversation from scratch."
        if not existing_rounds
        else "Continue the existing conversation naturally without rewriting any earlier turns."
    )
    existing_dialogue_json = (
        json.dumps([turn.model_dump() for turn in existing_rounds], indent=2, ensure_ascii=False)
        if existing_rounds
        else "[]"
    )

    system_prompt = f"""
You are generating an incremental chunk of an evaluation-grade User-Agent conversation for an agent system.

Your job is to produce only the NEXT chunk of turns.
This is conversation-only data. Do not simulate backend logs, tool calls, execution traces, MCP calls, or hidden chain-of-thought.

## Global requirements
- The output MUST be valid JSON only.
- The conversation must be between a user and an assistant-like agent.
- The assistant should sound like a capable agent: structured, thoughtful, helpful, concise when appropriate, and able to guide the conversation.
- The visible reply should remain natural. Do not expose hidden reasoning or say things like "my internal thought process".
- The dialogue must be concrete, information-rich, and easy to ask questions about later.
- Every turn should advance the conversation. No filler, no repetition, no generic padding.
- Stay faithful to the seed's central person, relationship, or scenario. Supporting entities may appear, but they must not take over the conversation unless the seed explicitly asks for that.
- Generate exactly {chunk_turn_count} NEW turns.
- The new turns must use turn_index values {start_turn} through {end_turn}.
- The entire output MUST be written in English even if the seed is written in another language.
- Do NOT mention tool names, APIs, MCP, trace files, runtime internals, or fake execution logs.
- Return only the appended chunk. Do not return any previous turns again.

## Ambient style constraints
- chat: the assistant behaves like a real conversational agent, not a human friend and not a logger.
- basic_info: the assistant keeps a professional but warm helper tone and respects role/context cues.

## Output schema
Return a JSON object with exactly one top-level key:
- new_dialogue_rounds

new_dialogue_rounds schema:
- turn_index: integer
- user_message: string
- agent_reply: string

## Module-specific constraints
{module_constraints}
""".strip()

    user_prompt = f"""
Seed preset: {prompt_seed_label(preset)}
Dialogue seed: {preset.dialogue_seed}
Recommended tone: {preset.tone_hint or "None"}
Target modules: {", ".join(target_modules)}
Instruction: {mode_instruction}

Existing dialogue rounds JSON:
{existing_dialogue_json}

Generate the next chunk now.
The conversation should feel like a real interaction someone would want to replay or analyze later, even though this script will not execute replay yet.
""".strip()

    return system_prompt, user_prompt


def build_chunk_revision_prompt(
    preset: SeedPreset,
    target_modules: list[str],
    *,
    existing_rounds: list[DialogueRound],
    chunk_turn_count: int,
    previous_json: str,
    local_issues: list[str],
) -> tuple[str, str]:
    system_prompt, base_user_prompt = build_chunk_prompt(
        preset,
        target_modules,
        existing_rounds=existing_rounds,
        chunk_turn_count=chunk_turn_count,
    )
    issue_text = "\n".join(f"- {issue}" for issue in local_issues) if local_issues else "- none"
    user_prompt = f"""
{base_user_prompt}

The previous chunk draft did not pass validation.
You must regenerate the full chunk JSON from scratch and fix every issue below.

Issues to fix:
{issue_text}

Previous draft:
{previous_json}
""".strip()
    return system_prompt, user_prompt


def build_metadata_prompt(
    preset: SeedPreset,
    target_modules: list[str],
    *,
    qa_seed: str,
    qa_seed_source: str,
    dialogue_rounds: list[DialogueRound],
) -> tuple[str, str]:
    dialogue_json = json.dumps([turn.model_dump() for turn in dialogue_rounds], indent=2, ensure_ascii=False)

    system_prompt = f"""
You are analyzing a completed evaluation-grade User-Agent conversation.

Return valid JSON only.
- All output must be in English.
- Do NOT mention tool names, APIs, MCP, trace files, runtime internals, or fake execution logs.

## Output schema
Return a JSON object with exactly these keys:
- module_coverage
- qa_items

module_coverage schema:
- memory?: {{facts: string[], time_anchors: string[], followup_hooks: string[]}}
- social_network?: {{entities: string[], relations: string[], contact_or_org_details: string[]}}
- awareness?: {{explicit_preferences: string[], agent_adaptations: string[]}}

qa_items schema:
- exactly 5 items
- each item has:
  - question: string
  - tester_hint_answer: one-line tester-facing hint, not a gold quote dump
  - target_modules: array containing only modules from this run
  - evidence_turns: integer array referencing turn_index values from the supplied conversation

## QA seed contract
- `dialogue_seed` describes what the conversation was supposed to contain.
- `qa_seed` describes what the QA set should evaluate.
- If qa_seed_source is `custom`, follow that custom QA seed rather than any default module QA focus.
- QA must still be answerable only from the supplied completed dialogue.
""".strip()

    user_prompt = f"""
Seed preset: {prompt_seed_label(preset)}
Dialogue seed: {preset.dialogue_seed}
Target modules: {", ".join(target_modules)}
QA seed source: {qa_seed_source}
QA seed:
{qa_seed}

Completed dialogue rounds JSON:
{dialogue_json}

Analyze the full conversation and return the JSON described above.
""".strip()
    return system_prompt, user_prompt


def build_self_review_prompt(
    preset: SeedPreset,
    target_modules: list[str],
    *,
    qa_seed: str,
    qa_seed_source: str,
    expected_turn_count: int,
    candidate_json: str,
    local_issues: list[str],
) -> tuple[str, str]:
    issue_text = "\n".join(f"- {issue}" for issue in local_issues) if local_issues else "- none"
    system_prompt = f"""
You are a strict reviewer for evaluation dialogue generation.

Review the candidate JSON against these standards:
- exactly {expected_turn_count} turns
- natural User-Agent dialogue, not logs or pseudo-tools
- rich, concrete, replay-worthy content
- all dialogue, coverage fields, and QA are in English
- exactly 5 QA items
- every QA is answerable from the dialogue
- each selected module satisfies its required detail level

Return JSON only with:
- passed: boolean
- issues: string[]
- revision_guidance: string[]
""".strip()

    user_prompt = f"""
Preset: {prompt_seed_label(preset)}
Dialogue seed: {preset.dialogue_seed}
Target modules: {", ".join(target_modules)}
QA seed source: {qa_seed_source}
QA seed:
{qa_seed}

Local validation findings:
{issue_text}

Candidate JSON:
{candidate_json}
""".strip()
    return system_prompt, user_prompt


async def call_json(
    client: AsyncOpenAI,
    *,
    model: str,
    system_prompt: str,
    user_prompt: str,
    temperature: float,
) -> str:
    try:
        response = await client.chat.completions.create(
            model=model,
            temperature=temperature,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
        )
    except Exception as exc:
        message = str(exc).lower()
        if "model" in message or "not found" in message or "does not exist" in message:
            raise RuntimeError(
                f"Model `{model}` is unavailable for the current endpoint/key. "
                f"Please verify the provider supports `{model}`.\nOriginal error: {exc}"
            ) from exc
        raise RuntimeError(f"OpenAI request failed: {exc}") from exc

    content = response.choices[0].message.content if response.choices else None
    if not content:
        raise RuntimeError("OpenAI returned an empty response.")
    return extract_json_block(content)


def extract_json_block(text: str) -> str:
    cleaned = text.strip()
    cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned)
    cleaned = re.sub(r"\s*```$", "", cleaned)
    for pattern in (r"\{[\s\S]*\}", r"\[[\s\S]*\]"):
        match = re.search(pattern, cleaned)
        if match:
            return match.group(0)
    raise RuntimeError(f"Model output was not valid JSON text:\n{cleaned[:500]}")


def contains_cjk(text: str) -> bool:
    return bool(CJK_RE.search(text))


def normalize_phrase_text(text: str) -> str:
    normalized = re.sub(r"[^a-z0-9]+", " ", text.lower())
    return re.sub(r"\s+", " ", normalized).strip()


def split_turns(total_turns: int, chunk_size: int) -> list[int]:
    chunks: list[int] = []
    remaining = total_turns
    while remaining > 0:
        current = min(chunk_size, remaining)
        chunks.append(current)
        remaining -= current
    return chunks


def validate_chunk_content(
    chunk: DialogueChunkContent,
    *,
    expected_turn_ids: list[int],
) -> list[str]:
    issues: list[str] = []
    actual_turn_ids = [turn.turn_index for turn in chunk.new_dialogue_rounds]

    if len(chunk.new_dialogue_rounds) != len(expected_turn_ids):
        issues.append(
            f"chunk must contain exactly {len(expected_turn_ids)} new turns, got {len(chunk.new_dialogue_rounds)}"
        )
    if actual_turn_ids != expected_turn_ids:
        issues.append(f"chunk turn_index values must be {expected_turn_ids}, got {actual_turn_ids}")

    for turn in chunk.new_dialogue_rounds:
        if not turn.user_message.strip():
            issues.append(f"turn {turn.turn_index} has empty user_message")
        if not turn.agent_reply.strip():
            issues.append(f"turn {turn.turn_index} has empty agent_reply")
        if contains_cjk(turn.user_message) or contains_cjk(turn.agent_reply):
            issues.append(f"turn {turn.turn_index} must be in English and should not contain CJK characters")

        text_bundle = f"{turn.user_message}\n{turn.agent_reply}".lower()
        for marker in FORBIDDEN_MARKERS:
            if marker in text_bundle:
                issues.append(f"turn {turn.turn_index} contains forbidden marker `{marker}`")

    return issues


def validate_generated_content(
    content: GeneratedContent,
    target_modules: list[str],
    *,
    expected_turn_count: int,
) -> list[str]:
    issues: list[str] = []
    turns = content.dialogue_rounds
    turn_count = len(turns)

    if turn_count != expected_turn_count:
        issues.append(f"dialogue_rounds must contain exactly {expected_turn_count} turns, got {turn_count}")

    for expected_idx, turn in enumerate(turns, start=1):
        if turn.turn_index != expected_idx:
            issues.append(f"turn_index must be sequential starting at 1, found {turn.turn_index} at position {expected_idx}")
        if not turn.user_message.strip():
            issues.append(f"turn {turn.turn_index} has empty user_message")
        if not turn.agent_reply.strip():
            issues.append(f"turn {turn.turn_index} has empty agent_reply")
        if contains_cjk(turn.user_message) or contains_cjk(turn.agent_reply):
            issues.append(f"turn {turn.turn_index} must be in English and should not contain CJK characters")

        text_bundle = f"{turn.user_message}\n{turn.agent_reply}".lower()
        for marker in FORBIDDEN_MARKERS:
            if marker in text_bundle:
                issues.append(f"turn {turn.turn_index} contains forbidden marker `{marker}`")

    if len(content.qa_items) != 5:
        issues.append(f"qa_items must contain exactly 5 items, got {len(content.qa_items)}")

    valid_turn_ids = {turn.turn_index for turn in turns}
    target_module_set = set(target_modules)
    for idx, item in enumerate(content.qa_items, start=1):
        if not item.question.strip():
            issues.append(f"qa_items[{idx}] has empty question")
        if not item.tester_hint_answer.strip():
            issues.append(f"qa_items[{idx}] has empty tester_hint_answer")
        if contains_cjk(item.question) or contains_cjk(item.tester_hint_answer):
            issues.append(f"qa_items[{idx}] must be in English and should not contain CJK characters")
        if not item.evidence_turns:
            issues.append(f"qa_items[{idx}] must include at least one evidence turn")
        if any(turn_id not in valid_turn_ids for turn_id in item.evidence_turns):
            issues.append(f"qa_items[{idx}] references out-of-range evidence_turns {item.evidence_turns}")
        if not item.target_modules:
            issues.append(f"qa_items[{idx}] must include at least one target module")
        extra_modules = [module for module in item.target_modules if module not in target_module_set]
        if extra_modules:
            issues.append(f"qa_items[{idx}] contains non-selected target modules: {extra_modules}")

    coverage = content.module_coverage
    if "memory" not in target_modules and coverage.memory is not None:
        issues.append("module_coverage.memory must be omitted when memory is not selected")
    if "social_network" not in target_modules and coverage.social_network is not None:
        issues.append("module_coverage.social_network must be omitted when social_network is not selected")
    if "awareness" not in target_modules and coverage.awareness is not None:
        issues.append("module_coverage.awareness must be omitted when awareness is not selected")

    if "memory" in target_modules:
        mem = coverage.memory
        if mem is None:
            issues.append("module_coverage.memory is required")
        else:
            values = mem.facts + mem.time_anchors + mem.followup_hooks
            if len(mem.facts) < 3:
                issues.append("memory coverage must include at least 3 facts")
            if len(mem.time_anchors) < 1:
                issues.append("memory coverage must include at least 1 time anchor")
            if len(mem.followup_hooks) < 1:
                issues.append("memory coverage must include at least 1 follow-up hook")
            if any(contains_cjk(value) for value in values):
                issues.append("module_coverage.memory must be in English and should not contain CJK characters")

    if "social_network" in target_modules:
        social = coverage.social_network
        if social is None:
            issues.append("module_coverage.social_network is required")
        else:
            values = social.entities + social.relations + social.contact_or_org_details
            if len(social.entities) < 2:
                issues.append("social_network coverage must include at least 2 entities")
            if len(social.relations) < 1:
                issues.append("social_network coverage must include at least 1 relation")
            if len(social.contact_or_org_details) < 1:
                issues.append("social_network coverage must include at least 1 contact or organization detail")
            if any(contains_cjk(value) for value in values):
                issues.append("module_coverage.social_network must be in English and should not contain CJK characters")

    if "awareness" in target_modules:
        awareness = coverage.awareness
        if awareness is None:
            issues.append("module_coverage.awareness is required")
        else:
            values = awareness.explicit_preferences + awareness.agent_adaptations
            if len(awareness.explicit_preferences) < 2:
                issues.append("awareness coverage must include at least 2 explicit preferences")
            if len(awareness.agent_adaptations) < 2:
                issues.append("awareness coverage must include at least 2 agent adaptations")
            if any(contains_cjk(value) for value in values):
                issues.append("module_coverage.awareness must be in English and should not contain CJK characters")

    return issues


def validate_catalog_qa_requirements(
    qa_items: list[QAItem],
    source_metadata: dict[str, Any] | None,
) -> list[str]:
    if not source_metadata or source_metadata.get("source") != "seed_catalog":
        return []

    required_phrases = source_metadata.get("required_qa_phrases") or []
    if not required_phrases:
        return []

    question_text = normalize_phrase_text("\n".join(item.question for item in qa_items))
    issues: list[str] = []
    for phrase in required_phrases:
        normalized = normalize_phrase_text(str(phrase))
        if normalized and normalized not in question_text:
            issues.append(
                f"catalog QA questions must include the natural phrase `{phrase}` at least once"
            )
    return issues


def build_postprocess_hints(target_modules: list[str]) -> list[str]:
    hints = [
        "This sample is conversation-only. Replay wiring, module injection, and persistence should be implemented in later stages.",
        "chat/basic_info are ambient style constraints only in v1 and are not standalone evaluation targets.",
    ]
    if "memory" in target_modules:
        hints.append(
            "For memory-oriented evaluation, later replay or post-processing should preserve stable facts, time anchors, and long-term follow-up hooks."
        )
    if "social_network" in target_modules:
        hints.append(
            "For social_network-oriented evaluation, later stages may need non-read-only entity extraction or persistence so named entities, relations, and org details are actually stored."
        )
    if "awareness" in target_modules:
        hints.append(
            "For awareness-oriented evaluation, later stages may need explicit preference update handling because this v1 generator does not call update_awareness."
        )
    return hints


def build_warnings(target_modules: list[str], *, partial: bool = False) -> list[str]:
    warnings = [
        "This output is synthetic evaluation dialogue only. No replay, database write, or module execution has been performed.",
        "Supported v1 targets are limited to memory, social_network, awareness, and job. RAG, matrix, and skill are intentionally excluded from this generator.",
    ]
    if partial:
        warnings.append(
            "This file is a partial incremental snapshot. dialogue_rounds are being accumulated, while module_coverage and qa_items are not finalized yet."
        )
    if target_modules:
        warnings.append(
            "Module coverage reflects dialogue evidence only, not confirmed runtime state inside the agent system."
        )
    return warnings


async def generate_chunk(
    client: AsyncOpenAI,
    *,
    preset: SeedPreset,
    target_modules: list[str],
    model: str,
    existing_rounds: list[DialogueRound],
    chunk_turn_count: int,
) -> DialogueChunkContent:
    last_candidate_json = ""
    local_issues: list[str] = []
    expected_turn_ids = list(range(len(existing_rounds) + 1, len(existing_rounds) + chunk_turn_count + 1))

    for attempt in range(2):
        if attempt == 0:
            system_prompt, user_prompt = build_chunk_prompt(
                preset,
                target_modules,
                existing_rounds=existing_rounds,
                chunk_turn_count=chunk_turn_count,
            )
        else:
            system_prompt, user_prompt = build_chunk_revision_prompt(
                preset,
                target_modules,
                existing_rounds=existing_rounds,
                chunk_turn_count=chunk_turn_count,
                previous_json=last_candidate_json,
                local_issues=local_issues,
            )

        raw_json = await call_json(
            client,
            model=model,
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            temperature=0.8 if attempt == 0 else 0.5,
        )
        last_candidate_json = raw_json

        try:
            chunk = DialogueChunkContent.model_validate_json(raw_json)
        except ValidationError as exc:
            local_issues = [f"chunk schema validation failed: {exc}"]
            continue

        local_issues = validate_chunk_content(
            chunk,
            expected_turn_ids=expected_turn_ids,
        )
        if not local_issues:
            return chunk

    issue_text = "\n".join(f"- {line}" for line in local_issues if line) or "- unknown"
    raise RuntimeError(
        f"Failed to generate a valid chunk for preset `{preset.name}` after 2 attempts.\n{issue_text}"
    )


async def generate_metadata(
    client: AsyncOpenAI,
    *,
    preset: SeedPreset,
    target_modules: list[str],
    qa_seed: str,
    qa_seed_source: str,
    model: str,
    dialogue_rounds: list[DialogueRound],
    source_metadata: dict[str, Any] | None = None,
) -> MetadataContent:
    last_candidate_json = ""
    local_issues: list[str] = []

    for attempt in range(2):
        system_prompt, user_prompt = build_metadata_prompt(
            preset,
            target_modules,
            qa_seed=qa_seed,
            qa_seed_source=qa_seed_source,
            dialogue_rounds=dialogue_rounds,
        )

        if attempt > 0:
            revision_text = "\n".join(f"- {issue}" for issue in local_issues if issue) or "- none"
            user_prompt = (
                f"{user_prompt}\n\n"
                "The previous metadata draft did not pass validation. Fix every issue below.\n"
                f"{revision_text}"
            )

        raw_json = await call_json(
            client,
            model=model,
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            temperature=0.3,
        )
        last_candidate_json = raw_json

        try:
            metadata = MetadataContent.model_validate_json(raw_json)
        except ValidationError as exc:
            local_issues = [f"metadata schema validation failed: {exc}"]
            continue

        local_issues = validate_generated_content(
            GeneratedContent(
                dialogue_rounds=dialogue_rounds,
                module_coverage=metadata.module_coverage,
                qa_items=metadata.qa_items,
            ),
            target_modules,
            expected_turn_count=len(dialogue_rounds),
        )
        local_issues.extend(
            validate_catalog_qa_requirements(
                metadata.qa_items,
                source_metadata,
            )
        )

        if not local_issues:
            return metadata

    issue_text = "\n".join(f"- {line}" for line in local_issues if line) or "- unknown"
    raise RuntimeError(
        f"Failed to generate metadata for preset `{preset.name}` after 2 attempts.\n{issue_text}\nLast draft:\n{last_candidate_json[:1000]}"
    )


def build_sample(
    *,
    sample_id: str,
    preset: SeedPreset,
    target_modules: list[str],
    qa_seed: str,
    qa_seed_source: str,
    content: GeneratedContent,
    source_metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    sample = {
        "sample_id": sample_id,
        "dialogue_seed": preset.dialogue_seed,
        "qa_seed": qa_seed,
        "qa_seed_source": qa_seed_source,
        "target_modules": target_modules,
        "dialogue_rounds": [turn.model_dump() for turn in content.dialogue_rounds],
        "module_coverage": content.module_coverage.model_dump(exclude_none=True),
        "qa_items": [item.model_dump() for item in content.qa_items],
        "postprocess_hints": build_postprocess_hints(target_modules),
        "warnings": build_warnings(target_modules),
    }
    if source_metadata:
        sample["seed_source"] = source_metadata
    return sample


def build_partial_sample(
    *,
    sample_id: str,
    preset: SeedPreset,
    target_modules: list[str],
    qa_seed: str,
    qa_seed_source: str,
    dialogue_rounds: list[DialogueRound],
    source_metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    sample = {
        "sample_id": sample_id,
        "dialogue_seed": preset.dialogue_seed,
        "qa_seed": qa_seed,
        "qa_seed_source": qa_seed_source,
        "target_modules": target_modules,
        "dialogue_rounds": [turn.model_dump() for turn in dialogue_rounds],
        "module_coverage": {},
        "qa_items": [],
        "postprocess_hints": build_postprocess_hints(target_modules),
        "warnings": build_warnings(target_modules, partial=True),
    }
    if source_metadata:
        sample["seed_source"] = source_metadata
    return sample


def resolve_output_path(
    out_arg: str | None,
    *,
    preset_name: str,
    single_sample: bool,
) -> Path:
    timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")

    if out_arg is None:
        DEFAULT_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
        return DEFAULT_OUTPUT_DIR / f"{timestamp}_{preset_name}.json"

    out_path = Path(out_arg)
    if single_sample:
        if out_path.suffix.lower() == ".json":
            out_path.parent.mkdir(parents=True, exist_ok=True)
            return out_path
        out_path.mkdir(parents=True, exist_ok=True)
        return out_path / f"{timestamp}_{preset_name}.json"

    if out_path.suffix.lower() == ".json":
        raise ValueError("`--out` must be a directory when generating multiple presets.")

    out_path.mkdir(parents=True, exist_ok=True)
    return out_path / f"{timestamp}_{preset_name}.json"


async def main() -> None:
    args = parse_args()
    try:
        module_override = parse_modules(args.modules)
        custom_qa_seed = load_custom_qa_seed(args)

        if args.seed_catalog:
            if args.preset:
                raise ValueError("`--preset` cannot be used together with `--seed-catalog`; use `--case-id` instead.")
            generation_inputs = load_seed_catalog_inputs(args.seed_catalog, args.case_id)
        else:
            if args.case_id:
                raise ValueError("`--case-id` requires `--seed-catalog`.")
            generation_inputs = [
                GenerationInput(
                    preset=preset,
                    source_metadata={
                        "source": "builtin_preset",
                        "preset_name": preset.name,
                    },
                )
                for preset in parse_presets(args.preset)
            ]
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc

    if args.turns < 1:
        raise SystemExit("`--turns` must be at least 1.")

    client = build_client()
    output_paths: list[Path] = []

    try:
        for generation_input in generation_inputs:
            preset = generation_input.preset
            target_modules = module_override or list(preset.recommended_modules)
            if custom_qa_seed is not None:
                qa_seed, qa_seed_source = custom_qa_seed, "custom"
            elif generation_input.qa_seed is not None:
                qa_seed = generation_input.qa_seed
                qa_seed_source = generation_input.qa_seed_source or "catalog"
            else:
                qa_seed, qa_seed_source = resolve_qa_seed(
                    custom_qa_seed=None,
                    target_modules=target_modules,
                )
            output_path = resolve_output_path(
                args.out,
                preset_name=preset.name,
                single_sample=(len(generation_inputs) == 1),
            )
            sample_id = f"{preset.name}_{datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')}"
            all_rounds: list[DialogueRound] = []
            chunk_plan = split_turns(args.turns, TURN_CHUNK_SIZE)

            for chunk_index, chunk_turn_count in enumerate(chunk_plan, start=1):
                chunk = await generate_chunk(
                    client,
                    preset=preset,
                    target_modules=target_modules,
                    model=args.model,
                    existing_rounds=all_rounds,
                    chunk_turn_count=chunk_turn_count,
                )
                all_rounds.extend(chunk.new_dialogue_rounds)

                partial_sample = build_partial_sample(
                    sample_id=sample_id,
                    preset=preset,
                    target_modules=target_modules,
                    qa_seed=qa_seed,
                    qa_seed_source=qa_seed_source,
                    dialogue_rounds=all_rounds,
                    source_metadata=generation_input.source_metadata,
                )
                output_path.write_text(
                    json.dumps(partial_sample, indent=2, ensure_ascii=False),
                    encoding="utf-8",
                )
                print(
                    f"[chunk {chunk_index}/{len(chunk_plan)}] {preset.name} "
                    f"generated turns {all_rounds[-chunk_turn_count].turn_index}-{all_rounds[-1].turn_index}"
                )

            metadata = await generate_metadata(
                client,
                preset=preset,
                target_modules=target_modules,
                qa_seed=qa_seed,
                qa_seed_source=qa_seed_source,
                model=args.model,
                dialogue_rounds=all_rounds,
                source_metadata=generation_input.source_metadata,
            )
            content = GeneratedContent(
                dialogue_rounds=all_rounds,
                module_coverage=metadata.module_coverage,
                qa_items=metadata.qa_items,
            )

            local_issues = validate_generated_content(
                content,
                target_modules,
                expected_turn_count=args.turns,
            )
            local_issues.extend(
                validate_catalog_qa_requirements(
                    content.qa_items,
                    generation_input.source_metadata,
                )
            )
            review_system, review_user = build_self_review_prompt(
                preset,
                target_modules,
                qa_seed=qa_seed,
                qa_seed_source=qa_seed_source,
                expected_turn_count=args.turns,
                candidate_json=json.dumps(
                    content.model_dump(mode="python", exclude_none=True),
                    indent=2,
                    ensure_ascii=False,
                ),
                local_issues=local_issues,
            )
            review_json = await call_json(
                client,
                model=args.model,
                system_prompt=review_system,
                user_prompt=review_user,
                temperature=0.2,
            )
            review = SelfReviewResult.model_validate_json(review_json)

            if local_issues or not review.passed:
                issue_text = "\n".join(
                    f"- {line}"
                    for line in [*local_issues, *review.issues, *review.revision_guidance]
                    if line
                ) or "- unknown"
                raise RuntimeError(
                    f"Final quality check failed for preset `{preset.name}`.\n{issue_text}"
                )

            sample = build_sample(
                sample_id=sample_id,
                preset=preset,
                target_modules=target_modules,
                qa_seed=qa_seed,
                qa_seed_source=qa_seed_source,
                content=content,
                source_metadata=generation_input.source_metadata,
            )
            output_path.write_text(
                json.dumps(sample, indent=2, ensure_ascii=False),
                encoding="utf-8",
            )
            output_paths.append(output_path)
            print(f"[ok] {preset.name} -> {output_path}")
    finally:
        await client.close()

    if output_paths:
        print(f"Generated {len(output_paths)} sample(s) with model `{args.model}`.")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except SystemExit:
        raise
    except Exception as exc:
        print(f"[error] {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
