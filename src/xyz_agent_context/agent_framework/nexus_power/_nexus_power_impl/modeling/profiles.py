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
"""

from __future__ import annotations

from xyz_agent_context.agent_framework.nexus_power.contracts.model import ProviderProfile

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
        max_output_tokens=8_192,
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
