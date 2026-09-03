"""
@file_name: test_baseline_trigger_map.py
@author: Bin Liang
@date: 2026-09-03
@description: Pin the registered channel trigger set (intent + what imports here).
"""
from __future__ import annotations

from tests.snapshots._approval import approve


def test_channel_trigger_registration_is_unchanged():
    from xyz_agent_context.module.channel_trigger_map import (
        CHANNEL_TRIGGER_MAP,
        REGISTERED_TRIGGER_CLASS_NAMES,
    )

    approve(
        "trigger_map",
        {
            "registered_class_names": sorted(REGISTERED_TRIGGER_CLASS_NAMES),
            "loaded_channels": sorted(CHANNEL_TRIGGER_MAP),
        },
    )
