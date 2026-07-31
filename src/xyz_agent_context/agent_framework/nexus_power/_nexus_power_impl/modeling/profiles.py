"""
@file_name: profiles.py
@author: Bin Liang
@date: 2026-07-29
@description: The ProviderProfile data table — onboarding a provider is
adding a row.

Rows encode measured dialect behaviour only (cache style, window,
argument-delta support). No row ever encodes a judgement about a
model's fitness (iron rule #15): unknown providers resolve to a
conservative default so every user model runs — it merely forgoes the
optimizations until its row is measured in.

Per-model limits are NOT this table's to invent: ``resolve_profile``
overlays ``providers/model_catalog``, the platform-wide source that
``adapters/openai_agents`` and ``llm/anthropic_helper`` already read. So
every framework that goes through our own client gets the same numbers,
and adding a model is one catalog row rather than one row per caller.

The rows here carry DIALECT only, keyed by protocol. That distinction
is load-bearing: ``provider`` is the resolved protocol, and one protocol
serves many vendors — NetMind's free-tier card speaks anthropic while
serving Qwen, DeepSeek and MiMo. Keying a ceiling off it would hand a 7B
model a 128K output request.

A ceiling set below what the model allows does not save anything: it
severs tool arguments mid-JSON, and the model cannot see the cut or
recover from it (2026-07-30 incident — a single write_file retried into
a loop against an 8_192 ceiling). Cost and depth are the caller's dials
(iron rule #15), not this table's.
"""

from __future__ import annotations

from dataclasses import replace
from typing import Callable

from xyz_agent_context.agent_framework.nexus_power.contracts.model import ProviderProfile
from xyz_agent_context.agent_framework.providers.model_catalog import (
    get_context_window,
    get_max_output_tokens,
)

_DEFAULT = ProviderProfile(name="default")

# name -> profile. Matching is by substring against provider then model
# (lowercased), first hit wins; order matters only for overlapping keys.
_PROFILES: tuple[ProviderProfile, ...] = (
    ProviderProfile(
        name="anthropic",
        cache_style="breakpoints",
        thinking_replay="strip",
        supports_arg_delta=True,
        context_window=200_000,
    ),
    ProviderProfile(
        name="deepseek",
        cache_style="prefix_auto",
        thinking_replay="strip",
        context_window=128_000,
    ),
    ProviderProfile(
        name="openai",
        cache_style="prefix_auto",
        thinking_replay="strip",
        supports_arg_delta=True,
        context_window=128_000,
    ),
    ProviderProfile(
        name="qwen",
        cache_style="none",
        context_window=32_000,
    ),
)


# Room left for the request framing the estimate cannot see (tool
# schemas, system preamble, the provider's own bookkeeping).
_HEADROOM_MARGIN_TOKENS = 4_096

# Below this an "answer" cannot carry a tool call worth making, so we
# stop shrinking and let the provider reject the request honestly rather
# than silently returning something useless.
_MIN_OUTPUT_TOKENS = 1_024


def builtin_profiles() -> dict[str, ProviderProfile]:
    """The current table, keyed by name (read-only view for tooling)."""
    return {p.name: p for p in _PROFILES}


def output_budget(profile: ProviderProfile, input_tokens_estimate: int) -> int:
    """The ``max_tokens`` to ask for, given what the input already costs.

    Anthropic enforces ``input + max_tokens <= context_window`` and
    answers a violation with a 400 that names neither "context window"
    nor any other marker the overflow table watches — so it would arrive
    as an unretryable INVALID_REQUEST and kill the turn.

    Measured on the dev gateway 2026-07-31: opus-4-8 accepted 144_065
    input tokens alongside ``max_tokens=128_000``, which puts its real
    window far beyond the 200_000 this module manages compaction
    against. So for the Opus and Sonnet rows this clamp never binds. It
    exists for Haiku, whose window really is 200_000: our compaction
    only trips at 150_000, leaving a band where an unclamped 64_000
    request would exceed the limit.
    """
    if input_tokens_estimate <= 0:
        return profile.max_output_tokens
    headroom = profile.output_wall - input_tokens_estimate - _HEADROOM_MARGIN_TOKENS
    return max(_MIN_OUTPUT_TOKENS, min(profile.max_output_tokens, headroom))


def resolve_profile(model: str, provider: str | None = None) -> ProviderProfile:
    """Dialect from the protocol, output limits from the model.

    The two halves have different keys and mixing them is a bug: an
    anthropic-protocol endpoint really does take cache_control
    breakpoints whatever it is serving, but how many tokens that model
    will emit is nothing to do with the protocol it speaks.
    """
    return _with_model_limits(_dialect_profile(model, provider), model)


def _dialect_profile(model: str, provider: str | None) -> ProviderProfile:
    """Match provider first, then model, by substring; default otherwise."""
    haystacks = [h.lower() for h in (provider or "", model or "") if h]
    for profile in _PROFILES:
        for haystack in haystacks:
            if profile.name in haystack:
                return profile
    # Anthropic-protocol endpoints frequently serve claude-* aliases.
    for haystack in haystacks:
        if "claude" in haystack:
            return builtin_profiles()["anthropic"]
    return _DEFAULT


def _with_model_limits(profile: ProviderProfile, model: str) -> ProviderProfile:
    """Overlay the platform's model catalog onto the dialect row.

    The catalog is the single source of truth for per-model limits and is
    already what ``adapters/openai_agents`` and ``llm/anthropic_helper``
    read — a second table here would be a second answer to the same
    question, and the two promptly disagreed (128_000 against the
    catalog's 115_200) the first time this was written locally.

    Unknown model → the dialect row's conservative defaults. A ceiling
    that is too small truncates arguments, which the model can at least
    be told about; one that is too large fails the request outright.
    """
    ceiling = _catalog_lookup(get_max_output_tokens, model)
    window = _catalog_lookup(get_context_window, model)
    if ceiling is None and window is None:
        return profile
    return replace(
        profile,
        max_output_tokens=ceiling or profile.max_output_tokens,
        vendor_context_window=window,
    )


def _catalog_lookup(getter: Callable[[str], int | None], model: str) -> int | None:
    """Catalog ids are exact. Retry once without a routing prefix so a
    platform id (``anthropic/claude-opus-4-8``) still resolves when only
    the bare id is registered — the reverse is already covered, both
    forms being registered for the NetMind entries."""
    if not model:
        return None
    found = getter(model)
    if found is None and "/" in model:
        found = getter(model.split("/", 1)[1])
    return found
