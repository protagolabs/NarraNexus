"""
@file_name: test_api_config_context_vars.py
@author: Bin Liang
@date: 2026-04-16
@description: New ContextVars provider_source / current_user_id default
behaviour and setter/getter roundtrip.
"""
from xyz_agent_context.agent_framework.api_config import (
    set_provider_source,
    get_provider_source,
    set_current_user_id,
    get_current_user_id,
)


def test_provider_source_default_none():
    set_provider_source(None)
    assert get_provider_source() is None


def test_provider_source_roundtrip():
    set_provider_source("system")
    assert get_provider_source() == "system"
    set_provider_source("user")
    assert get_provider_source() == "user"
    set_provider_source(None)
    assert get_provider_source() is None


def test_current_user_id_default_none():
    set_current_user_id(None)
    assert get_current_user_id() is None


def test_current_user_id_roundtrip():
    set_current_user_id("usr_x")
    assert get_current_user_id() == "usr_x"
    set_current_user_id(None)
    assert get_current_user_id() is None
