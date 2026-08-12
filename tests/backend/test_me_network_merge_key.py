"""
@file_name: test_me_network_merge_key.py
@author:
@date: 2026-08-12
@description: Pin the /me/network merge key (Mark's item 11).

Two different real people sharing a common name (e.g. two "王小明") were
collapsed into one node because the merge key used only entity_type + name.
The fix adds a stable cross-agent identity signal (email/phone from
contact_info) to the key. Same person seen by N agents (same contact, or no
contact at all) must still merge.
"""
from __future__ import annotations

from backend.routes.me import _entity_key


def test_same_name_different_email_do_not_merge():
    a = {"entity_name": "王小明", "contact_info": {"email": "eng@example.com"}}
    b = {"entity_name": "王小明", "contact_info": {"email": "chef@example.com"}}
    assert _entity_key("user", a) != _entity_key("user", b)


def test_same_name_same_email_merge():
    a = {"entity_name": "王小明", "contact_info": {"email": "eng@example.com"}}
    b = {"entity_name": "王小明", "contact_info": {"email": "eng@example.com"}}
    assert _entity_key("user", a) == _entity_key("user", b)


def test_same_name_no_contact_still_merges():
    a = {"entity_name": "kz"}
    b = {"entity_name": "kz"}
    assert _entity_key("user", a) == _entity_key("user", b)


def test_non_string_contact_values_do_not_crash():
    # attrs is LLM-authored JSON — phone may be an int, email a list, and the
    # /network merge loop has no try/except, so a raise here would 500 the graph.
    assert _entity_key("user", {"entity_name": "x", "contact_info": {"phone": 13800138000}})
    assert _entity_key("user", {"entity_name": "x", "contact_info": {"email": ["a@x.com"]}})
    assert _entity_key("user", {"entity_name": "x", "contact_info": "not-a-dict"})
