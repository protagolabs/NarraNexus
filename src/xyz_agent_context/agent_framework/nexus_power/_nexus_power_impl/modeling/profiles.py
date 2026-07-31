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

``max_output_tokens`` is the VENDOR maximum, never a house budget. A
ceiling set below what the model allows does not save anything: it
severs tool arguments mid-JSON, and the model cannot see the cut or
recover from it (2026-07-30 incident — a single write_file retried into
a loop against an 8_192 ceiling). Cost and depth are the caller's dials
(iron rule #15), not this table's.
"""

from __future__ import annotations

from xyz_agent_context.agent_framework.nexus_power.contracts.model import ProviderProfile

_DEFAULT = ProviderProfile(name="default")

# name -> profile. Matching is by substring against provider then model
# (lowercased), first hit wins; order matters only for overlapping keys.
_PROFILES: tuple[ProviderProfile, ...] = (
    # Ahead of "anthropic" on purpose: matching walks profiles in order
    # and the provider string alone ("anthropic") would otherwise claim
    # every Haiku request for a row whose ceiling Haiku cannot serve.
    ProviderProfile(
        name="haiku",
        cache_style="breakpoints",
        thinking_replay="strip",
        supports_arg_delta=True,
        context_window=200_000,
        max_output_tokens=64_000,  # vendor maximum for the Haiku line
    ),
    ProviderProfile(
        name="anthropic",
        cache_style="breakpoints",
        thinking_replay="strip",
        supports_arg_delta=True,
        context_window=200_000,
        max_output_tokens=128_000,  # vendor maximum for Opus and Sonnet
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


def builtin_profiles() -> dict[str, ProviderProfile]:
    """The current table, keyed by name (read-only view for tooling)."""
    return {p.name: p for p in _PROFILES}


def resolve_profile(model: str, provider: str | None = None) -> ProviderProfile:
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
