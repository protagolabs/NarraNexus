"""
@file_name: test_baseline_routes.py
@author: Bin Liang
@date: 2026-09-03
@description: Pin every (method, path, endpoint) the backend app registers.

Baseline for the plugin-platform refactor: route registration moves from
hand-written ``include_router`` lines to a registry, and this snapshot is the
proof that the set of routes did not change while that happens.
"""
from __future__ import annotations

from fastapi.routing import APIRoute

from tests.snapshots._approval import approve


def test_registered_routes_are_unchanged():
    from backend.main import app

    rows = sorted(
        [method, route.path, route.endpoint.__name__]
        for route in app.routes
        if isinstance(route, APIRoute)
        for method in route.methods
    )
    approve("routes", rows)
