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


# ------------------------------------------------------------------ boot-at-import


def register_builtins_for_import(registries: Registries) -> tuple[str, ...]:
    """Load every builtin manifest for the backend role at import time and drop disabled ones.

    ``backend.main`` mounts plugin routers at import (so the route table is
    fixed before serving and the approval snapshot can see it); the builtin
    contributions must therefore be registered by then. The lifespan boot
    repeats this idempotently and adds user plugins. Returns the disabled
    builtin ids (from registry.json ``builtin_overrides``).
    """
    from narranexus.kernel.deployment import is_cloud_mode
    from narranexus.kernel.plugins.compat import host_version
    from narranexus.kernel.plugins.install.builtin_deps import ensure_builtin_deps
    from narranexus.kernel.plugins.loader import discover, load

    cloud = is_cloud_mode()
    found = discover(cloud=cloud, host_version=host_version())
    for pid in found.disabled_builtins:
        registries.remove_owner(pid)
    loadable = []
    for manifest in found.manifests:
        if not manifest.is_builtin:
            continue
        status = ensure_builtin_deps(manifest, cloud=cloud)
        if status.ok:
            loadable.append(manifest)
        else:
            registries.remove_owner(manifest.id)
            logger.warning(f"[plugins] {manifest.id}: deps_missing at import — routes/workers not mounted: {status.error}")
    load(registries, loadable, role="backend")
    return tuple(found.disabled_builtins)


WORKERS_SLOT = "backend.workers"


@dataclass
class BackendWorkerContext:
    db: Any


async def start_backend_workers(app: Any, registries: Registries, db: Any) -> list[str]:
    """Start every ``backend.workers`` contribution with ``host == "backend"`` inside the API process."""
    from narranexus.contracts.worker import WorkerSpec

    started: list[str] = []
    handles: list[tuple[str, Any, Any]] = []
    if WORKERS_SLOT not in registries.paths() and WORKERS_SLOT not in registries.slots:
        return started
    for entry in registries.registry_for(WORKERS_SLOT).entries():
        try:
            spec = entry.factory()
        except Exception as exc:  # noqa: BLE001 — isolate the plugin
            logger.warning(f"[plugins] {entry.owner}: worker {entry.name!r} spec failed: {exc}")
            continue
        if not isinstance(spec, WorkerSpec) or spec.host != "backend":
            continue
        name = f"{entry.owner}:{spec.name}"
        try:
            handle = await spec.factory(BackendWorkerContext(db))
            task = asyncio.ensure_future(handle.run)
            task.add_done_callback(
                lambda t, n=name: logger.warning(f"[plugins] backend worker {n} exited: {t.exception()}")
                if not t.cancelled() and t.exception() is not None
                else None
            )
            handles.append((name, handle, task))
            started.append(name)
            logger.info(f"[plugins] backend worker {name} started")
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"[plugins] backend worker {name} failed to start: {exc}")
    app.state.plugin_backend_workers = handles
    return started


async def stop_backend_workers(app: Any) -> None:
    handles = getattr(app.state, "plugin_backend_workers", None) or []
    for name, handle, task in handles:
        try:
            result = handle.stop()
            if asyncio.iscoroutine(result):
                await result
            await asyncio.wait_for(task, timeout=10.0)
        except (asyncio.TimeoutError, asyncio.CancelledError):
            task.cancel()
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"[plugins] backend worker {name} stop raised: {exc}")
    app.state.plugin_backend_workers = []


__all__ = [
    "BackendWorkerContext",
    "LazyRouterApp",
    "MountReport",
    "ROUTES_SLOT",
    "WORKERS_SLOT",
    "mount_lazy_router",
    "mount_plugin_routes",
    "register_builtins_for_import",
    "start_backend_workers",
    "stop_backend_workers",
]
