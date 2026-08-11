"""
@file_name: test_channel_cred_roundtrip.py
@author:
@date: 2026-08-11
@description: Per-channel to_raw_dict <-> _cred_from_raw round-trip (blueprint
P2, #2). The seam's Direct<->Http parity (test_channel_store.py) proves the
DISPATCH; this proves each channel's own raw serialisation is a faithful inverse
so an HttpStore-backed lookup rebuilds byte-identical to the DirectStore object
— including the secret field(s), which to_public_dict deliberately drops.
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from xyz_agent_context.module.data_access.channel_store import (
    SUPPORTED_CHANNELS,
    _read_method_name,
)

_DT = datetime(2026, 8, 11, 3, 4, 5, tzinfo=timezone.utc)


def _cases():
    from xyz_agent_context.module.discord_module._discord_credential_manager import (
        DiscordCredential,
        _cred_from_raw as discord_from_raw,
    )
    from xyz_agent_context.module.slack_module._slack_credential_manager import (
        SlackCredential,
        _cred_from_raw as slack_from_raw,
    )
    from xyz_agent_context.module.telegram_module._telegram_credential_manager import (
        TelegramCredential,
        _cred_from_raw as telegram_from_raw,
    )
    from xyz_agent_context.module.wechat_module._wechat_credential_manager import (
        WeChatCredential,
        _cred_from_raw as wechat_from_raw,
    )
    from xyz_agent_context.module.narramessenger_module._narramessenger_credential_manager import (
        NarramessengerCredential,
        _cred_from_raw as narra_from_raw,
    )

    return [
        ("discord", DiscordCredential(agent_id="agent_x", bot_token="d-secret",
                                      bot_user_id="1", owner_user_id="o",
                                      created_at=_DT, updated_at=_DT), discord_from_raw,
         ["bot_token"]),
        ("slack", SlackCredential(agent_id="agent_x", bot_token="s-bot", app_token="s-app",
                                  team_id="T1", created_at=_DT, updated_at=_DT), slack_from_raw,
         ["bot_token", "app_token"]),
        ("telegram", TelegramCredential(agent_id="agent_x", bot_token="t-secret",
                                        bot_username="bot", created_at=_DT, updated_at=_DT),
         telegram_from_raw, ["bot_token"]),
        ("wechat", WeChatCredential(agent_id="agent_x", bot_token="w-secret",
                                    base_url="https://x", created_at=_DT, updated_at=_DT),
         wechat_from_raw, ["bot_token"]),
        ("narramessenger", NarramessengerCredential(
            agent_id="agent_x", bearer_token="n-bearer", matrix_access_token="syt_secret",
            matrix_homeserver_url="https://m", matrix_since_token="s_123",
            created_at=_DT, updated_at=_DT),
         narra_from_raw, ["bearer_token", "matrix_access_token"]),
    ]


@pytest.mark.parametrize("channel,cred,from_raw,secret_fields", _cases())
def test_raw_dict_roundtrips_and_carries_the_secret(channel, cred, from_raw, secret_fields):
    raw = cred.to_raw_dict()
    # the secret(s) the send tools need are present in the raw dict...
    for f in secret_fields:
        assert raw[f] == getattr(cred, f) and raw[f]
    # ...and NOT in the sanitised view (that is the whole point of two methods)
    public = cred.to_public_dict()
    for f in secret_fields:
        assert f not in public
    # inverse rebuilds an identical dataclass (datetimes survive ISO round-trip)
    assert from_raw(raw) == cred


def test_every_supported_channel_has_a_read_method():
    for channel in SUPPORTED_CHANNELS:
        assert _read_method_name(channel) in {"get", "get_credential"}
