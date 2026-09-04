"""
@file_name: __init__.py
@author: Bin Liang
@date: 2026-09-04
@description: The turn pipeline: seven stages, named strategies, profiles, boundary hooks.
"""
from narranexus.platform.turn.pipeline import PIPELINE_CONTRIBUTION, PROFILES_SLOT, TurnPipeline, resolve_profile


def ensure_pipeline_registered(registries=None) -> None:
    """Register the pipeline contribution (idempotent). The builtin profiles and
    default strategies are the ``builtin.turn`` plugin under plugins/ (batch 6b):
    the kernel registers them on first lookup (``resolve_profile`` /
    ``stages.ensure_registered``) — the platform never imports the plugin."""
    from narranexus.kernel.plugins.registries import KERNEL_REGISTRIES

    regs = registries or KERNEL_REGISTRIES
    pipeline = regs.registry_for("turn.pipeline")
    if PIPELINE_CONTRIBUTION.name not in pipeline:
        pipeline.register_contribution(PIPELINE_CONTRIBUTION, owner="builtin.turn")


ensure_pipeline_registered()

__all__ = ["PROFILES_SLOT", "TurnPipeline", "ensure_pipeline_registered", "resolve_profile"]
