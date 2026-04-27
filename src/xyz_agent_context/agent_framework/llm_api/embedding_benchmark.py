"""
@file_name: embedding_benchmark.py
@author: Codex
@date: 2026-04-16
@description: Helpers for benchmarking embedding request latency.
"""

from __future__ import annotations

import asyncio
import math
from dataclasses import dataclass
from pathlib import Path
from statistics import fmean, median
from time import perf_counter
from typing import Awaitable, Callable, Optional, Sequence

from xyz_agent_context.agent_framework.api_config import (
    ClaudeConfig,
    EmbeddingConfig,
    OpenAIConfig,
    set_user_config,
)
from xyz_agent_context.agent_framework.llm_api.embedding import get_embedding
from xyz_agent_context.agent_framework.provider_registry import (
    NETMIND_OPENAI_BASE_URL,
)


OFFICIAL_OPENAI_BASE_URL = "https://api.openai.com/v1"
DEFAULT_OPENAI_EMBEDDING_MODELS = ("text-embedding-3-small",)
DEFAULT_NETMIND_EMBEDDING_MODELS = (
    "BAAI/bge-m3",
    "nvidia/NV-Embed-v2",
    "dunzhang/stella_en_1.5B_v5",
)
DEFAULT_SAMPLE_SENTENCE = (
    "NarraNexus semantic retrieval benchmark sample. "
    "This text exercises embedding generation for memory search, "
    "entity linking, narrative routing, and multilingual context recall."
)


@dataclass(frozen=True)
class BenchmarkCase:
    provider: str
    model: str
    api_key: str
    base_url: str

    @property
    def label(self) -> str:
        return f"{self.provider}:{self.model}"


@dataclass(frozen=True)
class BenchmarkAttempt:
    duration_ms: float
    vector_dimensions: Optional[int] = None
    error: Optional[str] = None

    @property
    def succeeded(self) -> bool:
        return self.error is None


@dataclass(frozen=True)
class BenchmarkSummary:
    provider: str
    model: str
    base_url: str
    total_attempts: int
    success_count: int
    failure_count: int
    vector_dimensions: Optional[int]
    min_ms: Optional[float]
    max_ms: Optional[float]
    mean_ms: Optional[float]
    median_ms: Optional[float]
    p95_ms: Optional[float]

    @property
    def label(self) -> str:
        return f"{self.provider}:{self.model}"


@dataclass(frozen=True)
class BurstRoundSummary:
    round_index: int
    scheduled_offset_seconds: float
    actual_start_offset_seconds: float
    wall_time_seconds: float
    summary: BenchmarkSummary


@dataclass(frozen=True)
class BurstCaseSummary:
    provider: str
    model: str
    base_url: str
    burst_size: int
    round_count: int
    interval_seconds: float
    total_requests: int
    total_success_count: int
    total_failure_count: int
    total_elapsed_seconds: float
    rounds: list[BurstRoundSummary]
    overall_summary: BenchmarkSummary

    @property
    def label(self) -> str:
        return f"{self.provider}:{self.model}"


def build_benchmark_cases(
    openai_api_key: str,
    netmind_api_key: str,
    openai_models: Sequence[str] | None = None,
    netmind_models: Sequence[str] | None = None,
) -> list[BenchmarkCase]:
    """Build the provider/model matrix from repository defaults."""
    cases: list[BenchmarkCase] = []

    for model in openai_models or DEFAULT_OPENAI_EMBEDDING_MODELS:
        if openai_api_key:
            cases.append(
                BenchmarkCase(
                    provider="openai",
                    model=model,
                    api_key=openai_api_key,
                    base_url=OFFICIAL_OPENAI_BASE_URL,
                )
            )

    for model in netmind_models or DEFAULT_NETMIND_EMBEDDING_MODELS:
        if netmind_api_key:
            cases.append(
                BenchmarkCase(
                    provider="netmind",
                    model=model,
                    api_key=netmind_api_key,
                    base_url=NETMIND_OPENAI_BASE_URL,
                )
            )

    return cases


def build_sample_text(target_chars: int = 1536) -> str:
    """Build a deterministic text payload of at least the requested size."""
    if target_chars <= 0:
        raise ValueError("target_chars must be positive")

    chunks: list[str] = []
    total = 0
    while total < target_chars:
        chunks.append(DEFAULT_SAMPLE_SENTENCE)
        total += len(DEFAULT_SAMPLE_SENTENCE) + (1 if chunks else 0)
    return " ".join(chunks)


def read_dotenv_values(env_path: Path) -> dict[str, str]:
    """Read a .env file without performing variable expansion."""
    values: dict[str, str] = {}
    if not env_path.is_file():
        raise FileNotFoundError(f".env file not found: {env_path}")

    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
            value = value[1:-1]
        values[key.strip()] = value
    return values


def load_api_keys_from_env(env_path: Path) -> tuple[str, str]:
    """Load the two API keys needed by the benchmark script."""
    values = read_dotenv_values(env_path)
    return values.get("OPENAI_API_KEY", ""), values.get("NETMIND_API_KEY", "")


async def run_benchmark_case(
    case: BenchmarkCase,
    text: str,
    runs: int,
    timeout_seconds: float,
    warmup_runs: int = 1,
) -> tuple[list[BenchmarkAttempt], BenchmarkSummary]:
    """Execute warmup + measured embedding requests for one provider/model."""
    if runs <= 0:
        raise ValueError("runs must be positive")
    if warmup_runs < 0:
        raise ValueError("warmup_runs cannot be negative")

    for _ in range(warmup_runs):
        await _measure_single_request(case, text, timeout_seconds)

    attempts = [
        await _measure_single_request(case, text, timeout_seconds)
        for _ in range(runs)
    ]
    return attempts, summarize_attempts(case, attempts)


async def run_burst_case(
    case: BenchmarkCase,
    text: str,
    burst_size: int,
    rounds: int,
    interval_seconds: float,
    timeout_seconds: float,
    measure_fn: Optional[Callable[[BenchmarkCase, str, float], Awaitable[BenchmarkAttempt]]] = None,
) -> BurstCaseSummary:
    """Dispatch bursts at a fixed cadence, even if earlier bursts are still running."""
    if burst_size <= 0:
        raise ValueError("burst_size must be positive")
    if rounds <= 0:
        raise ValueError("rounds must be positive")
    if interval_seconds < 0:
        raise ValueError("interval_seconds cannot be negative")

    actual_measure = measure_fn or _measure_single_request
    started_at = perf_counter()

    tasks = [
        asyncio.create_task(
            _run_burst_round(
                case=case,
                text=text,
                round_index=round_index,
                burst_size=burst_size,
                scheduled_offset_seconds=(round_index - 1) * interval_seconds,
                timeout_seconds=timeout_seconds,
                started_at=started_at,
                measure_fn=actual_measure,
            )
        )
        for round_index in range(1, rounds + 1)
    ]

    round_results = await asyncio.gather(*tasks)
    elapsed_seconds = perf_counter() - started_at

    all_attempts: list[BenchmarkAttempt] = []
    round_summaries: list[BurstRoundSummary] = []
    for round_summary, attempts in sorted(round_results, key=lambda item: item[0].round_index):
        round_summaries.append(round_summary)
        all_attempts.extend(attempts)

    overall_summary = summarize_attempts(case, all_attempts)
    return BurstCaseSummary(
        provider=case.provider,
        model=case.model,
        base_url=case.base_url,
        burst_size=burst_size,
        round_count=rounds,
        interval_seconds=_round_seconds(interval_seconds),
        total_requests=len(all_attempts),
        total_success_count=overall_summary.success_count,
        total_failure_count=overall_summary.failure_count,
        total_elapsed_seconds=_round_seconds(elapsed_seconds),
        rounds=round_summaries,
        overall_summary=overall_summary,
    )


def summarize_attempts(case: BenchmarkCase, attempts: Sequence[BenchmarkAttempt]) -> BenchmarkSummary:
    """Aggregate benchmark attempts into user-facing summary stats."""
    successful = [attempt for attempt in attempts if attempt.succeeded]
    durations = sorted(attempt.duration_ms for attempt in successful)
    vector_dimensions = next(
        (attempt.vector_dimensions for attempt in successful if attempt.vector_dimensions is not None),
        None,
    )

    return BenchmarkSummary(
        provider=case.provider,
        model=case.model,
        base_url=case.base_url,
        total_attempts=len(attempts),
        success_count=len(successful),
        failure_count=len(attempts) - len(successful),
        vector_dimensions=vector_dimensions,
        min_ms=_round_metric(durations[0]) if durations else None,
        max_ms=_round_metric(durations[-1]) if durations else None,
        mean_ms=_round_metric(fmean(durations)) if durations else None,
        median_ms=_round_metric(median(durations)) if durations else None,
        p95_ms=_round_metric(_nearest_rank_percentile(durations, 0.95)) if durations else None,
    )


def format_summary_table(summaries: Sequence[BenchmarkSummary]) -> str:
    """Render summaries into a compact fixed-width table."""
    headers = (
        ("Provider", 8),
        ("Model", 28),
        ("OK", 4),
        ("Fail", 6),
        ("Dims", 6),
        ("Mean", 9),
        ("Median", 9),
        ("P95", 9),
        ("Min", 9),
        ("Max", 9),
    )

    def _fmt_metric(value: Optional[float]) -> str:
        return "-" if value is None else f"{value:.2f}ms"

    lines = [
        "  ".join(title.ljust(width) for title, width in headers),
        "  ".join("-" * width for _, width in headers),
    ]
    for summary in summaries:
        lines.append(
            "  ".join(
                [
                    summary.provider.ljust(8),
                    summary.model.ljust(28),
                    str(summary.success_count).rjust(4),
                    str(summary.failure_count).rjust(6),
                    str(summary.vector_dimensions or "-").rjust(6),
                    _fmt_metric(summary.mean_ms).rjust(9),
                    _fmt_metric(summary.median_ms).rjust(9),
                    _fmt_metric(summary.p95_ms).rjust(9),
                    _fmt_metric(summary.min_ms).rjust(9),
                    _fmt_metric(summary.max_ms).rjust(9),
                ]
            )
        )
    return "\n".join(lines)


def format_burst_summary(case_summary: BurstCaseSummary) -> str:
    """Render one burst-case summary as multiple human-readable lines."""
    lines = [
        f"{case_summary.label}",
        (
            "  total_elapsed="
            f"{case_summary.total_elapsed_seconds:.3f}s "
            f"requests={case_summary.total_requests} "
            f"ok={case_summary.total_success_count} "
            f"fail={case_summary.total_failure_count}"
        ),
        (
            "  overall_single_request: "
            f"mean={_fmt_seconds_from_ms(case_summary.overall_summary.mean_ms)} "
            f"median={_fmt_seconds_from_ms(case_summary.overall_summary.median_ms)} "
            f"p95={_fmt_seconds_from_ms(case_summary.overall_summary.p95_ms)} "
            f"max={_fmt_seconds_from_ms(case_summary.overall_summary.max_ms)}"
        ),
    ]
    for round_summary in case_summary.rounds:
        lines.append(
            (
                f"  round {round_summary.round_index}: "
                f"scheduled={round_summary.scheduled_offset_seconds:.3f}s "
                f"started={round_summary.actual_start_offset_seconds:.3f}s "
                f"wall={round_summary.wall_time_seconds:.3f}s "
                f"ok={round_summary.summary.success_count} "
                f"fail={round_summary.summary.failure_count} "
                f"mean={_fmt_seconds_from_ms(round_summary.summary.mean_ms)} "
                f"p95={_fmt_seconds_from_ms(round_summary.summary.p95_ms)} "
                f"max={_fmt_seconds_from_ms(round_summary.summary.max_ms)}"
            )
        )
    return "\n".join(lines)


async def _measure_single_request(
    case: BenchmarkCase,
    text: str,
    timeout_seconds: float,
) -> BenchmarkAttempt:
    """Measure a single request using the repository's embedding call path."""
    set_user_config(
        ClaudeConfig(),
        OpenAIConfig(api_key=case.api_key, base_url=case.base_url),
        EmbeddingConfig(api_key=case.api_key, base_url=case.base_url, model=case.model),
    )

    started = perf_counter()
    try:
        vector = await asyncio.wait_for(get_embedding(text, model=case.model), timeout=timeout_seconds)
    except Exception as exc:
        return BenchmarkAttempt(
            duration_ms=_round_metric((perf_counter() - started) * 1000),
            error=_format_error(exc),
        )

    return BenchmarkAttempt(
        duration_ms=_round_metric((perf_counter() - started) * 1000),
        vector_dimensions=len(vector),
    )


async def _run_burst_round(
    case: BenchmarkCase,
    text: str,
    round_index: int,
    burst_size: int,
    scheduled_offset_seconds: float,
    timeout_seconds: float,
    started_at: float,
    measure_fn: Callable[[BenchmarkCase, str, float], Awaitable[BenchmarkAttempt]],
) -> tuple[BurstRoundSummary, list[BenchmarkAttempt]]:
    target_started_at = started_at + scheduled_offset_seconds
    sleep_seconds = target_started_at - perf_counter()
    if sleep_seconds > 0:
        await asyncio.sleep(sleep_seconds)

    actual_started_at = perf_counter()
    attempts = await asyncio.gather(
        *[measure_fn(case, text, timeout_seconds) for _ in range(burst_size)]
    )
    wall_time_seconds = perf_counter() - actual_started_at
    round_summary = BurstRoundSummary(
        round_index=round_index,
        scheduled_offset_seconds=_round_seconds(scheduled_offset_seconds),
        actual_start_offset_seconds=_round_seconds(actual_started_at - started_at),
        wall_time_seconds=_round_seconds(wall_time_seconds),
        summary=summarize_attempts(case, attempts),
    )
    return round_summary, attempts


def _nearest_rank_percentile(values: Sequence[float], percentile: float) -> float:
    if not values:
        raise ValueError("values cannot be empty")
    if percentile <= 0:
        return values[0]
    if percentile >= 1:
        return values[-1]
    rank = max(1, math.ceil(percentile * len(values)))
    return values[rank - 1]


def _round_metric(value: float) -> float:
    return round(value, 2)


def _round_seconds(value: float) -> float:
    return round(value, 3)


def _fmt_seconds_from_ms(value_ms: Optional[float]) -> str:
    if value_ms is None:
        return "-"
    return f"{value_ms / 1000:.3f}s"


def _format_error(exc: Exception) -> str:
    message = str(exc).strip() or exc.__class__.__name__
    if len(message) > 180:
        message = f"{message[:177]}..."
    return f"{exc.__class__.__name__}: {message}"
