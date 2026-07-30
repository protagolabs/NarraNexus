"""Pricing resolution + cache-aware cost math.

Guards the 2026-07-30 finding: the two-entry hand-written MODEL_PRICING priced
NOTHING that was actually running (every llm_function / llm_stream / embedding
row booked at $0 across 2254 calls), and mispriced the one model it did list.
"""

import pytest

from xyz_agent_context.utils import model_pricing
from xyz_agent_context.utils.cost_tracker import calculate_cost
from xyz_agent_context.utils.model_pricing import price_for


@pytest.fixture(autouse=True)
def _fresh_pricing_cache():
    model_pricing.reset_cache_for_tests()
    yield
    model_pricing.reset_cache_for_tests()


# =========================================================================
# Resolution
# =========================================================================

def test_prices_a_model_the_old_table_never_knew():
    """claude-haiku-4-5 is what the helper actually runs — and was $0."""
    price = price_for("claude-haiku-4-5")
    assert price is not None
    assert price.input_per_token > 0
    assert price.output_per_token > 0
    assert price.source == "litellm"


def test_cli_family_alias_resolves_to_the_concrete_model():
    """The ledger's 207 bare-"haiku" rows must not stay unpriced.

    "haiku" is what _DEFAULT_CLAUDE_HELPER_MODEL puts on the wire, and users
    may type it into slot config themselves (iron rule #15), so it reaches
    cost accounting as ordinary input.
    """
    alias = price_for("haiku")
    concrete = price_for("claude-haiku-4-5")

    assert alias is not None, "bare family alias must still resolve to a price"
    assert alias.source == "litellm(alias)"
    assert alias.resolved_id == "claude-haiku-4-5"
    assert alias.input_per_token == concrete.input_per_token


def test_unknown_model_returns_none_rather_than_a_guess():
    """An aggregator id we have no rate card for stays unknown.

    None means "we do not know", and the caller still records the tokens. A
    fabricated price would look authoritative and be silently wrong — worse
    than a visible zero.
    """
    assert price_for("some-aggregator/never-heard-of-it-v9") is None


def test_unknown_model_warns_once_not_once_per_call():
    """2254 calls must not produce 2254 identical warnings.

    Repeated identical warnings are how people learn to filter a log; the
    silent logger.debug this replaced is how the $0 rows went unnoticed for
    months. One loud line per model is the balance.
    """
    from loguru import logger

    lines: list[str] = []
    sink_id = logger.add(lambda m: lines.append(str(m)), level="WARNING")
    try:
        for _ in range(5):
            price_for("some-aggregator/never-heard-of-it-v9")
    finally:
        logger.remove(sink_id)

    hits = [ln for ln in lines if "no price known" in ln]
    assert len(hits) == 1, hits
    assert "some-aggregator/never-heard-of-it-v9" in hits[0]


def test_empty_model_id_is_not_a_lookup():
    assert price_for("") is None


# =========================================================================
# Cost math — the cache buckets are the point
# =========================================================================

def test_cache_read_is_far_cheaper_than_full_rate_input():
    """The bug this whole change exists to make visible.

    Same token count, once as full-rate input and once as a cache read. If
    they price the same, the three Anthropic buckets have been collapsed
    somewhere and a cache-warm turn reads ~10x too expensive.
    """
    full = calculate_cost("claude-haiku-4-5", 100_000, 0)
    cached = calculate_cost("claude-haiku-4-5", 0, 0, cache_read_tokens=100_000)

    assert cached["total_cost"] < full["total_cost"] / 5
    assert cached["cache_cost"] > 0


def test_cache_write_costs_more_than_plain_input():
    plain = calculate_cost("claude-haiku-4-5", 10_000, 0)
    written = calculate_cost("claude-haiku-4-5", 0, 0, cache_creation_tokens=10_000)

    assert written["total_cost"] > plain["total_cost"]


def test_total_is_the_sum_of_its_parts():
    c = calculate_cost(
        "claude-haiku-4-5", 1_000, 500,
        cache_read_tokens=20_000, cache_creation_tokens=3_000,
    )
    assert c["total_cost"] == pytest.approx(
        c["input_cost"] + c["output_cost"] + c["cache_cost"]
    )


def test_unknown_model_costs_zero_but_still_returns_every_key():
    c = calculate_cost("some-aggregator/never-heard-of-it-v9", 5_000, 100,
                       cache_read_tokens=99)
    assert c["total_cost"] == 0.0
    assert set(c) == {"input_cost", "output_cost", "cache_cost", "total_cost"}


def test_unpriced_cache_falls_back_to_input_rate_not_to_free(monkeypatch):
    """A model with no published cache tier is not a model with free caching.

    Booking it at 0 would make "we turned caching on" look like it cut cost to
    nothing — the exact false win this change is meant to prevent.
    """
    from xyz_agent_context.utils.model_pricing import ModelPrice

    monkeypatch.setattr(
        "xyz_agent_context.utils.model_pricing.price_for",
        lambda _m: ModelPrice(
            input_per_token=1e-6, output_per_token=2e-6,
            cache_write_per_token=None, cache_read_per_token=None,
            resolved_id="x", source="test",
        ),
    )
    c = calculate_cost("x", 0, 0, cache_read_tokens=1_000, cache_creation_tokens=1_000)
    assert c["cache_cost"] == pytest.approx(2_000 * 1e-6)


# =========================================================================
# Regression: don't let a hand-maintained table come back
# =========================================================================

def test_gemini_flash_prices_at_the_real_rate_not_the_stale_hardcoded_one():
    """The removed table said $0.15/$0.60; the real rate is higher.

    A hand-written entry that nobody revisits is how the old table ended up
    half-price. If this ever matches the old numbers again, someone has
    reintroduced a local guess.
    """
    price = price_for("gemini-2.5-flash")
    assert price is not None
    assert price.input_per_token > 0.15 / 1_000_000


def test_litellm_failure_degrades_to_unknown_instead_of_raising(monkeypatch):
    """Pricing is observability; it may never become flow control."""
    import builtins

    real_import = builtins.__import__

    def _boom(name, *a, **kw):
        if name == "litellm":
            raise RuntimeError("upstream table unavailable")
        return real_import(name, *a, **kw)

    monkeypatch.setattr(builtins, "__import__", _boom)
    model_pricing.reset_cache_for_tests()

    assert price_for("claude-haiku-4-5") is None
    assert calculate_cost("claude-haiku-4-5", 10, 10)["total_cost"] == 0.0
