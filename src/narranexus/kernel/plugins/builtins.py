"""
@file_name: builtins.py
@author: Bin Liang
@date: 2026-09-03
@description: The host's own plugins, declared as manifests (D4: builtins are plugins too).

Explicit registration, not discovery (Python Packaging guide: explicit is
deterministic, greppable and fast): ``BUILTIN_PLUGINS`` is the ordered list of
(plugin id, package) pairs and the manifest of each is the plugin's own
``narranexus-plugin.json`` — read from the package (a wheel carries it) or the
source checkout. The kernel keeps no copy: a plugin's ``provides`` names its
``Contribution`` symbols and the loader registers exactly those at boot.
"""
from __future__ import annotations


import json
from functools import lru_cache
from importlib.resources import files
from pathlib import Path
from typing import Any

from narranexus.kernel.plugins.manifest import Manifest, parse_manifest
from narranexus.kernel.plugins.slots import SlotTree, build_kernel_slot_tree

# Stage-1 load order = this order. The id names the plugin directory
# (plugins/<id>/) and the package its manifest ships in
# (narranexus_plugins.<package>); the manifest itself is the JSON file — the
# kernel holds NO copy of it (charter: one knowledge, one home; adding a
# builtin does not touch a 338-line dict here any more).
BUILTIN_PLUGINS: tuple[tuple[str, str], ...] = (
    ("builtin.frameworks.nexus_power", "frameworks_nexus_power"),
    ("builtin.frameworks.claude_code", "frameworks_claude_code"),
    ("builtin.frameworks.codex_cli", "frameworks_codex_cli"),
    ("builtin.providers", "providers"),
    ("builtin.llm_clients", "llm_clients"),
    ("builtin.memory_kinds", "memory_kinds"),
    ("builtin.prompts", "prompts"),
    ("builtin.nexus_plugins_module", "nexus_plugins_module"),
    ("builtin.turn", "turn"),
    ("builtin.awareness", "awareness_module"),
    ("builtin.basic_info", "basic_info_module"),
    ("builtin.chat", "chat_module"),
    ("builtin.social_network", "social_network_module"),
    ("builtin.job", "job_module"),
    ("builtin.skills", "skill_module"),
    ("builtin.message_bus", "message_bus_module"),
    ("builtin.common_tools", "common_tools_module"),
    ("builtin.general_memory", "general_memory_module"),
    ("builtin.home_assistant", "home_assistant_module"),
    ("builtin.channels.lark", "lark_module"),
    ("builtin.channels.slack", "slack_module"),
    ("builtin.channels.telegram", "telegram_module"),
    ("builtin.channels.wechat", "wechat_module"),
    ("builtin.channels.narramessenger", "narramessenger_module"),
    ("builtin.channels.discord", "discord_module"),
    ("builtin.teams", "teams"),
    ("builtin.auth.local", "auth_local"),
    ("builtin.auth.netmind", "auth_netmind"),
    ("builtin.ui", "ui"),
)
MANIFEST_FILENAME = "narranexus-plugin.json"


def _manifest_path(plugin_id: str, package: str) -> Path:
    """The plugin's manifest: the copy packaged inside the wheel (hatch force-include) first, the source checkout second."""
    try:
        packaged = files(f"narranexus_plugins.{package}") / MANIFEST_FILENAME
        if packaged.is_file():
            return Path(str(packaged))
    except (ModuleNotFoundError, TypeError):
        pass
    checkout = Path(__file__).resolve().parents[4] / "plugins" / plugin_id / MANIFEST_FILENAME
    if checkout.is_file():
        return checkout
    raise FileNotFoundError(f"{plugin_id}: {MANIFEST_FILENAME} not found in package narranexus_plugins.{package} nor at {checkout}")


def _read_manifest_data() -> tuple[dict[str, Any], ...]:
    out: list[dict[str, Any]] = []
    for plugin_id, package in BUILTIN_PLUGINS:
        data = json.loads(_manifest_path(plugin_id, package).read_text(encoding="utf-8"))
        if data.get("id") != plugin_id:
            raise ValueError(f"{plugin_id}: its manifest declares id {data.get('id')!r}")
        out.append(data)
    return tuple(out)


# The builtin manifests as parsed JSON, in load order (read once per process).
BUILTIN_MANIFEST_DATA: tuple[dict[str, Any], ...] = _read_manifest_data()


def load_builtins(registries: Any, role: str = "backend", *, distribution: Any = None) -> Any:
    """Register every builtin's contributions for ``role`` into ``registries`` — the
    one way a process (or a test) gets the builtins WITHOUT the host boot's
    discovery, marker and write-back. What ``hosts.boot`` does in stage 1;
    private registries in tests use it directly. Honours a distribution's
    picks when one is given. Does not freeze.
    """
    from narranexus.kernel.plugins.loader import load

    manifests = list(builtin_manifests())
    if distribution is not None:
        selected = {m.id for m in distribution.manifests}
        manifests = [m for m in manifests if m.id in selected]
    return load(registries, manifests, role=role)  # type: ignore[arg-type]



def build_builtin_manifests(tree: SlotTree) -> tuple[Manifest, ...]:
    """Validate the builtin manifest data against ``tree`` (uncached).

    Pass 1 declares every builtin's own slots into ``tree`` (a plugin's
    ``declares`` need no tree to parse), so pass 2 can check each manifest's
    ``provides`` against the slots the OTHER builtins declare —
    ``builtin.frameworks.*`` provide into ``turn.pipeline.act.framework``,
    which ``builtin.turn`` declares, whatever their relative order.
    """
    tree.declare_all(slot for data in BUILTIN_MANIFEST_DATA for slot in Manifest.model_validate(data).declared_slots())
    return tuple(parse_manifest(data, tree=tree, allow_builtin=True) for data in BUILTIN_MANIFEST_DATA)


def slot_tree_with_builtins() -> SlotTree:
    """The kernel tree plus every slot a builtin plugin declares (e.g. ``builtin.turn``'s stage slots).

    User-plugin validation (install, discover, publish-check, self-extension)
    must see these: a third-party Recall strategy provides into
    ``turn.pipeline.recall``, which only exists once ``builtin.turn`` declared it.
    """
    tree = build_kernel_slot_tree()
    tree.declare_all(slot for manifest in builtin_manifests() for slot in manifest.declared_slots())
    return tree


@lru_cache(maxsize=1)
def builtin_manifests() -> tuple[Manifest, ...]:
    """Validated builtin manifests against the kernel tree (cached; the data is a constant)."""
    return build_builtin_manifests(build_kernel_slot_tree())


__all__ = ["BUILTIN_MANIFEST_DATA", "BUILTIN_PLUGINS", "MANIFEST_FILENAME", "build_builtin_manifests", "builtin_manifests", "load_builtins", "slot_tree_with_builtins"]
