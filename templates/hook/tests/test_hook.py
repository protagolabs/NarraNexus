import asyncio
from pathlib import Path

from narranexus.sdk.testing import PluginTestHost

PLUGIN_DIR = Path(__file__).resolve().parents[1]


def test_hook_fires(tmp_path):
    with PluginTestHost(PLUGIN_DIR, tmp_path / "home") as host:
        asyncio.run(host.hooks.caller("onDidPersistTurn").call(run_id="r1", agent_id="a1", user_id="u1", event_id="e1", narrative_ids=[]))
        from narranexus.kernel.plugins.importer import import_plugin_module

        assert import_plugin_module(host.plugin_id).SEEN == ["a1:r1"]
