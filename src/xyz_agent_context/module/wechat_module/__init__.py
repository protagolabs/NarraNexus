"""
@file_name: __init__.py
@author:
@date: 2026-06-24
@description: WeChat (iLink) channel module package — re-exports WeChatModule.
"""

from .descriptor import DESCRIPTOR as _DESCRIPTOR  # noqa: F401 — registers this channel's WorkingSource before any module class body reads it
from .wechat_module import WeChatModule

__all__ = ["WeChatModule"]
