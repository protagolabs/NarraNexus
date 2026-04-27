"""
@file_name: test_embedding_benchmark.py
@author: Codex
@date: 2026-04-16
@description: Unit tests for the embedding benchmark helper module.
"""

import asyncio

from xyz_agent_context.agent_framework.provider_registry import (
    NETMIND_OPENAI_BASE_URL,
)

from xyz_agent_context.agent_framework.llm_api.embedding_benchmark import (
    BenchmarkAttempt,
    build_benchmark_cases,
    build_sample_text,
    run_burst_case,
    summarize_attempts,
)


def test_build_benchmark_cases_uses_expected_provider_models():
    cases = build_benchmark_cases(
        openai_api_key="openai-key",
        netmind_api_key="netmind-key",
    )

    assert [(case.provider, case.model) for case in cases] == [
        ("openai", "text-embedding-3-small"),
        ("netmind", "BAAI/bge-m3"),
        ("netmind", "nvidia/NV-Embed-v2"),
        ("netmind", "dunzhang/stella_en_1.5B_v5"),
    ]
    assert cases[0].base_url == "https://api.openai.com/v1"
    assert all(case.api_key for case in cases)
    assert all(case.base_url == NETMIND_OPENAI_BASE_URL for case in cases[1:])


def test_build_sample_text_is_deterministic_and_hits_target_length():
    text_a = build_sample_text(target_chars=512)
    text_b = build_sample_text(target_chars=512)

    assert text_a == text_b
    assert len(text_a) >= 512
    assert "semantic retrieval" in text_a


def test_summarize_attempts_counts_failures_and_computes_latency_stats():
    summary = summarize_attempts(
        build_benchmark_cases("openai-key", "netmind-key")[1],
        [
            BenchmarkAttempt(duration_ms=120.0, vector_dimensions=1024),
            BenchmarkAttempt(duration_ms=180.0, vector_dimensions=1024),
            BenchmarkAttempt(duration_ms=140.0, vector_dimensions=1024),
            BenchmarkAttempt(duration_ms=0.0, error="timeout"),
        ],
    )

    assert summary.provider == "netmind"
    assert summary.model == "BAAI/bge-m3"
    assert summary.total_attempts == 4
    assert summary.success_count == 3
    assert summary.failure_count == 1
    assert summary.vector_dimensions == 1024
    assert summary.min_ms == 120.0
    assert summary.max_ms == 180.0
    assert summary.mean_ms == 146.67
    assert summary.median_ms == 140.0
    assert summary.p95_ms == 180.0


async def test_run_burst_case_starts_rounds_on_fixed_interval():
    case = build_benchmark_cases("openai-key", "netmind-key")[0]
    started_at: list[float] = []

    async def fake_measure(*_args, **_kwargs):
        started_at.append(asyncio.get_running_loop().time())
        await asyncio.sleep(0.02)
        return BenchmarkAttempt(duration_ms=20.0, vector_dimensions=1536)

    result = await run_burst_case(
        case=case,
        text="payload",
        burst_size=3,
        rounds=2,
        interval_seconds=0.05,
        timeout_seconds=1.0,
        measure_fn=fake_measure,
    )

    assert result.total_requests == 6
    assert result.total_success_count == 6
    assert result.total_failure_count == 0
    assert len(result.rounds) == 2
    assert result.rounds[0].summary.success_count == 3
    assert result.rounds[1].summary.success_count == 3
    assert result.rounds[0].scheduled_offset_seconds == 0.0
    assert result.rounds[1].scheduled_offset_seconds == 0.05
    assert result.rounds[0].wall_time_seconds >= 0.02
    assert result.rounds[1].wall_time_seconds >= 0.02
    assert result.total_elapsed_seconds >= 0.07

    first_round_start = min(started_at[:3])
    second_round_start = min(started_at[3:])
    assert second_round_start - first_round_start >= 0.04


async def test_run_burst_case_aggregates_failures():
    case = build_benchmark_cases("openai-key", "netmind-key")[1]
    call_count = 0

    async def fake_measure(*_args, **_kwargs):
        nonlocal call_count
        call_count += 1
        if call_count % 2 == 0:
            return BenchmarkAttempt(duration_ms=10.0, error="boom")
        return BenchmarkAttempt(duration_ms=10.0, vector_dimensions=1024)

    result = await run_burst_case(
        case=case,
        text="payload",
        burst_size=2,
        rounds=2,
        interval_seconds=0.01,
        timeout_seconds=1.0,
        measure_fn=fake_measure,
    )

    assert result.total_requests == 4
    assert result.total_success_count == 2
    assert result.total_failure_count == 2
    assert result.overall_summary.success_count == 2
    assert result.overall_summary.failure_count == 2
