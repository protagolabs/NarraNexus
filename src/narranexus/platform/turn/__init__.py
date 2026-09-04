"""
@file_name: __init__.py
@author: Bin Liang
@date: 2026-09-04
@description: The turn pipeline: seven stages, named strategies, profiles, boundary hooks.
"""
from narranexus.platform.turn.pipeline import PIPELINE_CONTRIBUTION, PROFILES_SLOT, TurnPipeline, resolve_profile
from narranexus.platform.turn.profiles import BUILTIN_PROFILES, PROFILE_CONTRIBUTIONS


def ensure_pipeline_registered(registries=None) -> None:
    """Register the pipeline + builtin profiles (idempotent) so the ``builtin.turn`` manifest load is a no-op.

    Lives here (not in ``stages``) because ``pipeline`` imports ``stages``;
    the same import-time registration pattern the frameworks use.
    """
    from narranexus.kernel.plugins.registries import KERNEL_REGISTRIES

    regs = registries or KERNEL_REGISTRIES
    pipeline = regs.registry_for("turn.pipeline")
    if PIPELINE_CONTRIBUTION.name not in pipeline:
        pipeline.register_contribution(PIPELINE_CONTRIBUTION, owner="builtin.turn")
    profiles = regs.registry_for(PROFILES_SLOT)
    for contribution in PROFILE_CONTRIBUTIONS:
        if contribution.name not in profiles:
            profiles.register_contribution(contribution, owner="builtin.turn")


ensure_pipeline_registered()

__all__ = ["BUILTIN_PROFILES", "PROFILE_CONTRIBUTIONS", "TurnPipeline", "ensure_pipeline_registered", "resolve_profile"]
