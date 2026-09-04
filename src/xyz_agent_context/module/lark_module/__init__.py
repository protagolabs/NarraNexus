"""
@file_name: __init__.py
@date: 2026-04-10
@description: LarkModule — Lark/Feishu integration for messaging, contacts, docs, calendar, tasks
"""

from .descriptor import DESCRIPTOR as _DESCRIPTOR  # noqa: F401 — registers this channel's WorkingSource before any module class body reads it
from .lark_module import LarkModule

__all__ = ["LarkModule"]
