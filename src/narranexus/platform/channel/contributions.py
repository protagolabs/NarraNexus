"""
@file_name: contributions.py
@author: Bin Liang
@date: 2026-09-07
@description: One ChannelDescriptor → the plugin's module and trigger contributions. A channel plugin writes its descriptor once; its ``contribution.py`` derives MODULES / TRIGGERS from it instead of restating the class refs in three places.
"""
from __future__ import annotations

import importlib
from typing import Any, Callable

from narranexus.contracts.channel import ChannelDescriptor
from narranexus.contracts.trigger import TriggerSpec
from narranexus.kernel.plugins.registry import Contribution


def _loader(ref: str) -> Callable[[], Any]:
    module_path, attr = ref.rsplit(":", 1)
    return lambda: getattr(importlib.import_module(module_path), attr)


def module_contribution(class_ref: str, plugin_id: str, *, channel: bool) -> Contribution[type]:
    """``agent.capabilities.modules`` entry for a module class named ``pkg.mod:Class`` (imported lazily)."""
    return Contribution(class_ref.rsplit(":", 1)[1], _loader(class_ref), meta={"plugin_id": plugin_id, "channel": channel})


def trigger_contribution(spec: TriggerSpec) -> Contribution[TriggerSpec]:
    """``ingress.triggers`` entry carrying the spec (the class is resolved by the consumer, per channel, so one missing dependency isolates one channel)."""
    return Contribution(spec.name, lambda: spec, meta={"host": spec.host, "class_ref": spec.class_ref})


def contributions_from(descriptor: ChannelDescriptor, plugin_id: str) -> tuple[tuple[Contribution[type], ...], tuple[Contribution[TriggerSpec], ...]]:
    """(MODULES, TRIGGERS) a channel plugin contributes, derived from its descriptor."""
    modules = (module_contribution(descriptor.module_ref, plugin_id, channel=True),) if descriptor.module_ref else ()
    triggers = (trigger_contribution(TriggerSpec(descriptor.name, descriptor.trigger_ref)),) if descriptor.trigger_ref else ()
    return modules, triggers


__all__ = ["contributions_from", "module_contribution", "trigger_contribution"]
