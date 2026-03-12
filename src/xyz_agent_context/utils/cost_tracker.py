"""
@file_name: cost_tracker.py
@author: Bin Liang
@date: 2026-03-12
@description: LLM API cost calculation and recording utility

Provides centralized cost tracking for all LLM API calls:
- Claude (agent_loop)
- OpenAI GPT (llm_function)
- Gemini (llm_function)
- OpenAI Embedding

Architecture:
    Pure functions + async recorder. Not a Module — this is infrastructure.
    Price table is hardcoded but centralized for easy future migration to settings.
"""

from __future__ import annotations

from typing import Dict, Optional

from loguru import logger


# =============================================================================
# Price Table (per million tokens, USD)
# =============================================================================

MODEL_PRICING: Dict[str, Dict[str, float]] = {
    # Claude
    "claude-sonnet-4-20250514": {"input": 3.0, "output": 15.0},
    "claude-sonnet-4-2025-05-14": {"input": 3.0, "output": 15.0},
    # OpenAI
    "gpt-5.1-2025-11-13": {"input": 2.0, "output": 8.0},
    # Gemini
    "gemini-2.5-flash": {"input": 0.15, "output": 0.60},
    # Embedding
    "text-embedding-3-small": {"input": 0.02, "output": 0.0},
    "text-embedding-3-large": {"input": 0.13, "output": 0.0},
}


def calculate_cost(
    model: str,
    input_tokens: int,
    output_tokens: int,
) -> dict:
    """
    Calculate cost for a single API call.

    Args:
        model: Model identifier (must be a key in MODEL_PRICING)
        input_tokens: Number of input tokens consumed
        output_tokens: Number of output tokens consumed

    Returns:
        {"input_cost": float, "output_cost": float, "total_cost": float}
        All values in USD. Returns zeros for unknown models.
    """
    pricing = MODEL_PRICING.get(model)
    if pricing is None:
        logger.warning(f"Unknown model for cost calculation: {model}")
        return {"input_cost": 0.0, "output_cost": 0.0, "total_cost": 0.0}

    input_cost = (input_tokens / 1_000_000) * pricing["input"]
    output_cost = (output_tokens / 1_000_000) * pricing["output"]
    return {
        "input_cost": input_cost,
        "output_cost": output_cost,
        "total_cost": input_cost + output_cost,
    }


async def record_cost(
    db,
    agent_id: str,
    event_id: Optional[str],
    call_type: str,
    model: str,
    input_tokens: int,
    output_tokens: int,
    sdk_cost_usd: Optional[float] = None,
) -> None:
    """
    Calculate cost and persist a record to the database.

    Args:
        db: AsyncDatabaseClient instance
        agent_id: Agent that incurred the cost
        event_id: Associated event (None for standalone llm_function / embedding calls)
        call_type: "agent_loop" | "llm_function" | "embedding"
        model: Model identifier
        input_tokens: Input token count
        output_tokens: Output token count
        sdk_cost_usd: SDK-calculated cost (used as fallback when model is unknown)
    """
    cost = calculate_cost(model, input_tokens, output_tokens)
    # Use SDK-provided cost when our price table doesn't recognize the model
    final_cost = cost["total_cost"] if cost["total_cost"] > 0 else (sdk_cost_usd or 0.0)
    try:
        await db.insert("cost_records", {
            "agent_id": agent_id,
            "event_id": event_id,
            "call_type": call_type,
            "model": model,
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "total_cost_usd": final_cost,
        })
        logger.debug(
            f"Cost recorded: agent={agent_id} model={model} "
            f"tokens={input_tokens}+{output_tokens} cost=${final_cost:.6f}"
            f"{' (sdk)' if cost['total_cost'] == 0 and sdk_cost_usd else ''}"
        )
    except Exception as e:
        # Cost tracking failure should never block the main flow
        logger.error(f"Failed to record cost: {e}")
