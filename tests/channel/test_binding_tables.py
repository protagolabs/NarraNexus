"""
@file_name: test_binding_tables.py
@author: Bin Liang
@date: 2026-09-07
@description: The agents-directory channel UNION is built with every value bound as a parameter: the channel name never lands in the SQL text, aliases are indexes, and a descriptor name that could break the query is rejected by ChannelDescriptor itself.
"""
from __future__ import annotations

import pytest

from narranexus.contracts.channel import ChannelDescriptor, CredentialSchema
from narranexus.platform.channel.binding_tables import BindingSource, bound_channels_query, channel_binding_sources


def test_query_binds_channel_names_and_ids_as_parameters():
    sources = [
        BindingSource("lark", "(SELECT agent_id, enabled FROM channel_credentials WHERE channel = %s) AS src_0", ("lark",), "enabled"),
        BindingSource("home_assistant", "instance_homeassistant_bindings", (), None),
    ]
    query, params = bound_channels_query(sources, ["a1", "a2"])
    assert query.count(" UNION ALL ") == 1
    assert "'lark'" not in query and "src_lark" not in query
    assert params == ("lark", "lark", "a1", "a2", "home_assistant", "a1", "a2")
    assert query.count("%s") == len(params)


def test_builtin_sources_use_index_aliases():
    import narranexus.platform.module_system  # noqa: F401 — registers the builtin descriptors

    sources = channel_binding_sources()
    im = [s for s in sources if s.params]
    assert im and all(s.source.endswith(f"AS src_{i}") for i, s in enumerate(im))
    assert all(s.params == (s.channel,) for s in im)


@pytest.mark.parametrize("bad", ["lark'; DROP TABLE x; --", "Lark", "café", "a-b"])
def test_descriptor_rejects_names_that_are_not_plain_identifiers(bad):
    with pytest.raises(ValueError):
        ChannelDescriptor(name=bad, display_name="x", transport="webhook", credential_schema=CredentialSchema(fields=(), supports_test=False))
