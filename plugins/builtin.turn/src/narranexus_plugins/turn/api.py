"""
@file_name: api.py
@author: Bin Liang
@date: 2026-09-04
@description: Public facade of builtin.turn: the builtin profiles and default strategies by name.
"""
from __future__ import annotations

from narranexus_plugins.turn.profiles import BUILTIN_PROFILES
from narranexus_plugins.turn.stages import STRATEGIES

PLUGIN_ID = "builtin.turn"
PACKAGE = "narranexus_plugins.turn"

__all__ = ["BUILTIN_PROFILES", "PACKAGE", "PLUGIN_ID", "STRATEGIES"]
