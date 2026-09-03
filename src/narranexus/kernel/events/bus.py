"""
@file_name: bus.py
@author: Bin Liang
@date: 2026-09-03
@description: In-process host event bus — the substrate the ``hooks`` kind rides on.

Emitters (the platform, at its observation points) call ``emit(name,
payload)``; subscribers (plugins, the analytics sink) get a ``Disposable``
back from ``subscribe``. Handler failures are isolated and counted per owner
so a broken plugin degrades its own metrics, never the turn. Handlers are
delivered concurrently and each runs under a timeout — synchronous handlers on
a worker thread so a blocking one can be abandoned — and one that exceeds it
is counted as ``slow``: the platform must never become the interruption
source (binding rule #15), but a plugin must not be allowed to hold the turn
hostage either. Worst-case emit latency is therefore one timeout, not one per
subscriber.

Only declared event names may be subscribed to or emitted: the host vocabulary
in ``narranexus.contracts.events.HOST_EVENTS`` plus anything a plugin
``declare``s through its manifest. An unknown name is ``UnknownEntry`` — a
typo fails at subscription time instead of silently never firing.
"""
from __future__ import annotations

import asyncio
import inspect
from collections import Counter
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Iterable, Mapping

from loguru import logger

from narranexus.contracts import Disposable, RegistryConflict, UnknownEntry
from narranexus.contracts.events import HOST_EVENTS

Handler = Callable[[Mapping[str, Any]], Any | Awaitable[Any]]


@dataclass(frozen=True)
class Subscription:
    handler: Handler
    owner: str


@dataclass
class EmitReport:
    event: str
    delivered: int = 0
    failed: list[tuple[str, BaseException]] = field(default_factory=list)
    timed_out: list[str] = field(default_factory=list)


class EventBus:
    def __init__(self, known: Iterable[str] = HOST_EVENTS, *, timeout_s: float = 0.2) -> None:
        self._subs: dict[str, list[Subscription]] = {name: [] for name in known}
        self.timeout_s = timeout_s
        self.error_counts: Counter[str] = Counter()
        self.slow_counts: Counter[str] = Counter()

    # ------------------------------------------------------------ vocabulary

    def declare(self, name: str) -> None:
        if name in self._subs:
            raise RegistryConflict(f"event {name!r} already declared")
        self._subs[name] = []

    def names(self) -> tuple[str, ...]:
        return tuple(self._subs)

    def _subs_for(self, name: str) -> list[Subscription]:
        try:
            return self._subs[name]
        except KeyError:
            raise UnknownEntry(f"event {name!r} is not declared. Known: {list(self._subs)}") from None

    # ---------------------------------------------------------- subscription

    def subscribe(self, name: str, handler: Handler, *, owner: str) -> Disposable:
        subs = self._subs_for(name)
        sub = Subscription(handler, owner)
        subs.append(sub)

        def _dispose() -> None:
            if sub in subs:
                subs.remove(sub)

        return Disposable(_dispose)

    def subscriber_count(self, name: str) -> int:
        return len(self._subs_for(name))

    def block(self, owner: str) -> int:
        removed = 0
        for subs in self._subs.values():
            before = len(subs)
            subs[:] = [s for s in subs if s.owner != owner]
            removed += before - len(subs)
        return removed

    # ------------------------------------------------------------------ emit

    async def emit(self, name: str, payload: Mapping[str, Any]) -> EmitReport:
        """Deliver to every subscriber concurrently; worst case is one timeout, not N."""
        report = EmitReport(event=name)
        subs = list(self._subs_for(name))
        outcomes = await asyncio.gather(
            *(self._deliver(sub, payload) for sub in subs), return_exceptions=True
        )
        for sub, outcome in zip(subs, outcomes):
            if outcome is None:
                report.delivered += 1
            elif isinstance(outcome, asyncio.TimeoutError):
                self.slow_counts[sub.owner] += 1
                report.timed_out.append(sub.owner)
                logger.warning(
                    f"[events:{name}] handler of {sub.owner!r} exceeded {self.timeout_s}s and was abandoned"
                )
            elif isinstance(outcome, BaseException):
                self.error_counts[sub.owner] += 1
                report.failed.append((sub.owner, outcome))
                logger.warning(f"[events:{name}] handler of {sub.owner!r} failed: {outcome!r}")
        return report

    async def _deliver(self, sub: Subscription, payload: Mapping[str, Any]) -> None:
        await asyncio.wait_for(self._invoke(sub.handler, payload), timeout=self.timeout_s)

    @staticmethod
    async def _invoke(handler: Handler, payload: Mapping[str, Any]) -> None:
        if inspect.iscoroutinefunction(handler):
            await handler(payload)
            return
        # A synchronous handler runs on a worker thread so a blocking one
        # (time.sleep, a requests call) can be abandoned by wait_for instead
        # of freezing the event loop. Its result may still be awaitable.
        result = await asyncio.to_thread(handler, payload)
        if inspect.isawaitable(result):
            await result


__all__ = ["EventBus", "EmitReport", "Handler", "Subscription"]
