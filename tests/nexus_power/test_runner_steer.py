"""
@file_name: test_runner_steer.py
@author: Bin Liang
@date: 2026-08-21
@description: The runner's steer-line parser. Post-request stdin lines carry
live-steering injections as {"steer": {provider msg}}; a malformed or
non-steer line must be ignored (never take the turn down).
"""

from xyz_agent_context.agent_framework.nexus_power.runner import parse_steer_line


def test_valid_steer_line_returns_the_message():
    msg = parse_steer_line('{"steer": {"role": "user", "content": "hi"}}')
    assert msg == {"role": "user", "content": "hi"}


def test_non_steer_object_is_ignored():
    assert parse_steer_line('{"something_else": 1}') is None


def test_malformed_json_is_ignored():
    assert parse_steer_line("not json at all") is None
    assert parse_steer_line("") is None


def test_steer_that_is_not_an_object_is_ignored():
    assert parse_steer_line('{"steer": "just a string"}') is None
    assert parse_steer_line('{"steer": null}') is None
