"""
@file_name: plugin_settings_store.py
@author: Bin Liang
@date: 2026-09-03
@description: Database-backed ``SettingsStore`` for plugin settings (table ``plugin_settings``), secrets Fernet-encrypted.

The kernel's ``PluginSettings`` is synchronous over a snapshot; this store
bridges to the async database client by running its statements on the
client's loop (``run_coroutine_threadsafe`` when called from another thread,
direct ``run_until_complete`` on a private loop when no loop is running).
Secrets are encrypted with the platform ``SecretBox`` before they hit the
row and decrypted on load, so a database dump never contains a plaintext
plugin credential.
"""
from __future__ import annotations

import asyncio
import json
from typing import Any, Awaitable, Mapping, TypeVar

from xyz_agent_context.marketplace._skill_marketplace_impl.secret_box import SecretBox, get_secret_box

T = TypeVar("T")

TABLE = "plugin_settings"


def _run(coro: Awaitable[T]) -> T:
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None
    if loop is None:
        return asyncio.run(coro)  # type: ignore[arg-type]
    # Called from inside a running loop's thread: the caller must not block the
    # loop, so hop to a worker thread that drives a private loop.
    import concurrent.futures

    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
        return pool.submit(lambda: asyncio.run(coro)).result()  # type: ignore[arg-type]


class DbSettingsStore:
    """``SettingsStore`` over ``AsyncDatabaseClient``."""

    def __init__(self, db: Any, *, secret_box: SecretBox | None = None) -> None:
        self._db = db
        self._box = secret_box

    def _secret_box(self) -> SecretBox:
        if self._box is None:
            self._box = get_secret_box()
        return self._box

    async def load_async(self, plugin_id: str) -> dict[str, Any]:
        rows = await self._db.get(TABLE, {"plugin_id": plugin_id})
        out: dict[str, Any] = {}
        for row in rows:
            raw = row.get("value_json")
            if raw is None:
                continue
            if int(row.get("is_secret") or 0):
                out[row["key"]] = self._secret_box().decrypt(raw)
            else:
                out[row["key"]] = json.loads(raw)
        return out

    async def save_async(self, plugin_id: str, key: str, value: Any, *, secret: bool) -> None:
        stored = self._secret_box().encrypt(str(value)) if secret else json.dumps(value)
        existing = await self._db.get_one(TABLE, {"plugin_id": plugin_id, "key": key})
        data = {"value_json": stored, "is_secret": 1 if secret else 0}
        if existing:
            await self._db.update(TABLE, {"plugin_id": plugin_id, "key": key}, data)
        else:
            await self._db.insert(TABLE, {"plugin_id": plugin_id, "key": key, **data})

    async def delete_async(self, plugin_id: str, key: str) -> None:
        await self._db.delete(TABLE, {"plugin_id": plugin_id, "key": key})

    # ---- SettingsStore (sync) protocol

    def load(self, plugin_id: str) -> Mapping[str, Any]:
        return _run(self.load_async(plugin_id))

    def save(self, plugin_id: str, key: str, value: Any, *, secret: bool) -> None:
        _run(self.save_async(plugin_id, key, value, secret=secret))

    def delete(self, plugin_id: str, key: str) -> None:
        _run(self.delete_async(plugin_id, key))


__all__ = ["DbSettingsStore", "TABLE"]
