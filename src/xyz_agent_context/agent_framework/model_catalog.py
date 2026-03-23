"""
@file_name: model_catalog.py
@author: Bin Liang
@date: 2026-03-23
@description: Static model catalog for each provider preset

Provides available model lists grouped by provider and slot type.
This is a static preset; models will be updated as providers add new ones.

Usage:
    from xyz_agent_context.agent_framework.model_catalog import (
        get_models_for_slot,
        get_default_model,
        get_embedding_dimensions,
    )
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from xyz_agent_context.schema.provider_schema import ProviderPreset, SlotName


# =============================================================================
# Model Definition
# =============================================================================

@dataclass(frozen=True)
class ModelInfo:
    """Metadata for a single model"""
    model_id: str                        # e.g. "BAAI/bge-m3"
    display_name: str                    # e.g. "BGE-M3 (Multilingual)"
    slot_types: list[str]                # Which slots can use this: ["embedding"], ["agent"], ["helper_llm"]
    dimensions: Optional[int] = None     # Embedding dimensions (only for embedding models)
    is_default: bool = False             # Whether this is the preset default


# =============================================================================
# Model Catalog
# =============================================================================

# --- NetMind Models ---
NETMIND_MODELS: list[ModelInfo] = [
    # Agent models (Anthropic protocol)
    ModelInfo(
        model_id="minimax/minimax-m2.5",
        display_name="MiniMax M2.5",
        slot_types=["agent"],
        is_default=True,
    ),
    # Helper LLM models (OpenAI protocol)
    ModelInfo(
        model_id="minimax/minimax-m2.5",
        display_name="MiniMax M2.5",
        slot_types=["helper_llm"],
        is_default=True,
    ),
    ModelInfo(
        model_id="google/gemini-3.1-pro-preview",
        display_name="Gemini 3.1 Pro",
        slot_types=["helper_llm"],
    ),
    ModelInfo(
        model_id="google/gemini-3.1-flash-lite-preview",
        display_name="Gemini 3.1 Flash Lite",
        slot_types=["helper_llm"],
    ),
    ModelInfo(
        model_id="moonshotai/Kimi-K2.5",
        display_name="Kimi K2.5",
        slot_types=["helper_llm"],
    ),
    ModelInfo(
        model_id="zai-org/GLM-5",
        display_name="GLM-5",
        slot_types=["helper_llm"],
    ),
    ModelInfo(
        model_id="deepseek-ai/DeepSeek-V3",
        display_name="DeepSeek V3",
        slot_types=["helper_llm"],
    ),
    # Embedding models (OpenAI protocol)
    ModelInfo(
        model_id="BAAI/bge-m3",
        display_name="BGE-M3 (Multilingual)",
        slot_types=["embedding"],
        dimensions=1024,
        is_default=True,
    ),
    ModelInfo(
        model_id="nvidia/NV-Embed-v2",
        display_name="NV-Embed-v2",
        slot_types=["embedding"],
        dimensions=4096,
    ),
    ModelInfo(
        model_id="dunzhang/stella_en_1.5B_v5",
        display_name="Stella EN 1.5B v5",
        slot_types=["embedding"],
        dimensions=1024,
    ),
]

# --- OpenAI Models ---
OPENAI_MODELS: list[ModelInfo] = [
    # Helper LLM models
    ModelInfo(
        model_id="gpt-5.1-2025-11-13",
        display_name="GPT-5.1",
        slot_types=["helper_llm"],
        is_default=True,
    ),
    # Embedding models
    ModelInfo(
        model_id="text-embedding-3-small",
        display_name="Embedding 3 Small (1536d)",
        slot_types=["embedding"],
        dimensions=1536,
        is_default=True,
    ),
    ModelInfo(
        model_id="text-embedding-3-large",
        display_name="Embedding 3 Large (3072d)",
        slot_types=["embedding"],
        dimensions=3072,
    ),
]

# --- Anthropic Models ---
ANTHROPIC_MODELS: list[ModelInfo] = [
    ModelInfo(
        model_id="claude-opus-4-6",
        display_name="Claude Opus 4.6",
        slot_types=["agent"],
        is_default=True,
    ),
    ModelInfo(
        model_id="claude-sonnet-4-6",
        display_name="Claude Sonnet 4.6",
        slot_types=["agent"],
    ),
]

# --- Claude OAuth (same models as Anthropic, but no key needed) ---
CLAUDE_OAUTH_MODELS: list[ModelInfo] = ANTHROPIC_MODELS


# =============================================================================
# Catalog Registry
# =============================================================================

_CATALOG: dict[str, list[ModelInfo]] = {
    ProviderPreset.NETMIND: NETMIND_MODELS,
    ProviderPreset.OPENAI: OPENAI_MODELS,
    ProviderPreset.ANTHROPIC: ANTHROPIC_MODELS,
    ProviderPreset.CLAUDE_OAUTH: CLAUDE_OAUTH_MODELS,
    # Custom providers have no preset catalog; users specify models manually
}


# =============================================================================
# Query Functions
# =============================================================================

def get_models_for_slot(preset: str | ProviderPreset, slot: str | SlotName) -> list[ModelInfo]:
    """
    Get available models for a given provider preset and slot type.

    Args:
        preset: Provider preset name (e.g. "netmind")
        slot: Slot name (e.g. "embedding")

    Returns:
        List of ModelInfo that can be used in this slot
    """
    preset_str = preset.value if isinstance(preset, ProviderPreset) else preset
    slot_str = slot.value if isinstance(slot, SlotName) else slot
    models = _CATALOG.get(preset_str, [])
    return [m for m in models if slot_str in m.slot_types]


def get_default_model(preset: str | ProviderPreset, slot: str | SlotName) -> Optional[ModelInfo]:
    """
    Get the default model for a given provider preset and slot type.

    Returns:
        The default ModelInfo, or None if no default is set
    """
    candidates = get_models_for_slot(preset, slot)
    for m in candidates:
        if m.is_default:
            return m
    return candidates[0] if candidates else None


def get_embedding_dimensions(model_id: str) -> Optional[int]:
    """
    Look up the embedding dimensions for a given model ID.

    Searches all catalogs. Returns None if the model is not found
    or is not an embedding model.
    """
    for models in _CATALOG.values():
        for m in models:
            if m.model_id == model_id and m.dimensions is not None:
                return m.dimensions
    return None


def get_all_presets_summary() -> dict[str, list[dict]]:
    """
    Get a summary of all presets and their models for API/frontend use.

    Returns:
        Dict mapping preset name to list of model dicts
    """
    result = {}
    for preset_name, models in _CATALOG.items():
        result[preset_name] = [
            {
                "model_id": m.model_id,
                "display_name": m.display_name,
                "slot_types": m.slot_types,
                "dimensions": m.dimensions,
                "is_default": m.is_default,
            }
            for m in models
        ]
    return result
