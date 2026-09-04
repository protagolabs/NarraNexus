"""
@file_name: plugins_host.py
@author: Bin Liang
@date: 2026-09-03
@description: The backend host's consumption of plugin contributions: mounting ``backend.routes`` entries.

Called once from ``backend.main`` after the shell's own routers and before
the SPA fallback, so a plugin route can never shadow a shell route and the
catch-all can never swallow a plugin route. Non-builtin owners are forced
under ``/api/x/<plugin id>``; a spec that claims another prefix is refused
(the plugin is reported, not mounted). ``auth="none"`` routers are recorded
as auth-exempt prefixes — the only way a plugin obtains a public endpoint,
and always an explicit one.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable

from loguru import logger

from narranexus.contracts.route import RouterSpec, plugin_route_prefix
from narranexus.kernel.plugins.registries import Registries

from backend import auth as _auth

ROUTES_SLOT = "backend.routes"


class LazyRouterApp:
    """An ASGI app mounted at a plugin's prefix that builds the real router on the first request.

    The plugin's route handlers are imported (its activation) only when
    someone actually calls a route under its prefix — so a hundred installed
    plugins cost nothing at boot. The first request awaits ``activate``
    (a coroutine returning the ``RouterSpec``); a failure is remembered and
    answered as 503 with the plugin id, never retried in a tight loop.
    """

    def __init__(self, prefix: str, activate: Callable[[], Awaitable[RouterSpec]], *, plugin_id: str) -> None:
        self.prefix = prefix
        self.plugin_id = plugin_id
        self._activate = activate
        self._app: Any = None
        self._error: str | None = None
        self._lock = asyncio.Lock()

    async def _ensure(self) -> Any:
        if self._app is not None or self._error is not None:
            return self._app
        async with self._lock:
            if self._app is None and self._error is None:
                try:
                    from fastapi import FastAPI

                    spec = await self._activate()
                    sub = FastAPI(openapi_url=None, docs_url=None, redoc_url=None)
                    sub.include_router(spec.router)
                    self._app = sub
                except Exception as exc:  # noqa: BLE001 - isolate the plugin
                    self._error = f"{type(exc).__name__}: {exc}"
                    logger.warning(f"[plugins] {self.plugin_id}: lazy router failed: {self._error}")
        return self._app

    async def __call__(self, scope: Any, receive: Any, send: Any) -> None:
        app = await self._ensure()
        if app is None:
            from starlette.responses import JSONResponse

            response = JSONResponse({"detail": f"plugin {self.plugin_id} failed to activate", "error": self._error}, status_code=503)
            await response(scope, receive, send)
            return
        await app(scope, receive, send)

    @property
    def activated(self) -> bool:
        return self._app is not None

    @property
    def error(self) -> str | None:
        return self._error


def mount_lazy_router(app: Any, plugin_id: str, prefix: str, activate: Callable[[], Awaitable[RouterSpec]]) -> LazyRouterApp:
    """Mount a lazy router for a user plugin under its own prefix (the auth middleware still runs first)."""
    if not prefix.startswith(plugin_route_prefix(plugin_id)):
        raise ValueError(f"{plugin_id}: lazy router prefix {prefix!r} is outside {plugin_route_prefix(plugin_id)!r}")
    lazy = LazyRouterApp(prefix, activate, plugin_id=plugin_id)
    app.mount(prefix, lazy, name=f"plugin:{plugin_id}")
    return lazy


@dataclass
class MountReport:
    mounted: list[tuple[str, str, str]] = field(default_factory=list)  # (owner, name, prefix)
    refused: list[tuple[str, str, str]] = field(default_factory=list)  # (owner, name, reason)


def _check_prefix(owner: str, spec: RouterSpec) -> str | None:
    if owner.startswith("builtin."):
        return None
    allowed = plugin_route_prefix(owner)
    if spec.prefix == allowed or spec.prefix.startswith(allowed + "/"):
        return None
    return f"prefix {spec.prefix!r} is outside {allowed!r}"


def mount_plugin_routes(app: Any, registries: Registries) -> MountReport:
    """Mount every ``backend.routes`` contribution onto ``app``."""
    report = MountReport()
    registry = registries.registry_for(ROUTES_SLOT)
    for entry in registry.entries():
        try:
            spec = entry.factory()
        except Exception as exc:  # noqa: BLE001 — a broken factory must not take the host down
            report.refused.append((entry.owner, entry.name, f"{type(exc).__name__}: {exc}"))
            logger.warning(f"[plugins] {entry.owner}: route {entry.name!r} factory failed: {exc}")
            continue
        if not isinstance(spec, RouterSpec):
            report.refused.append((entry.owner, entry.name, f"not a RouterSpec: {type(spec).__name__}"))
            continue
        problem = _check_prefix(entry.owner, spec)
        if problem:
            report.refused.append((entry.owner, entry.name, problem))
            logger.warning(f"[plugins] {entry.owner}: route {entry.name!r} refused: {problem}")
            continue
        if spec.auth == "none":
            # Module attribute, not an import-time binding: tests swap the set.
            _auth.PLUGIN_EXEMPT_PREFIXES.add(spec.prefix + "/")
            _auth.PLUGIN_EXEMPT_PREFIXES.add(spec.prefix)
        app.include_router(spec.router, prefix=spec.prefix, tags=list(spec.tags) or [entry.owner])
        report.mounted.append((entry.owner, entry.name, spec.prefix))
    if report.mounted:
        logger.info(f"[plugins] mounted {len(report.mounted)} plugin router(s): {[m[2] for m in report.mounted]}")
    return report


__all__ = ["LazyRouterApp", "MountReport", "ROUTES_SLOT", "mount_lazy_router", "mount_plugin_routes"]
