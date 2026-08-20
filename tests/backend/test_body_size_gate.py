"""
@file_name: test_body_size_gate.py
@date: 2026-08-20
@description: Drift pins for the body-size middleware (review #334 r4 I2):
the gate's existence depends on strings in two files staying aligned, and
neither a route move nor an unmounted middleware would fail any other test.

- every BODY_CAPS pattern must match a route REGISTERED on the real app
  (paths sampled from the live route table, not hand-copied strings);
- the middleware must actually be mounted on the app;
- the mount order must keep body_size inside access_log (its 413s belong
  in the access log) and outside the routes.
"""
from __future__ import annotations

from backend.main import app
from backend.middleware.body_size import BODY_CAPS, body_size_middleware

_SAMPLE = {
    "{agent_id}": "a",
    "{artifact_id}": "b",
    "{token}": "t",
    "{port}": "1",
    "{path:path}": "x",
}


def _sampled_routes():
    out = []
    for r in app.routes:
        path = getattr(r, "path", "")
        for k, v in _SAMPLE.items():
            path = path.replace(k, v)
        out.append((getattr(r, "methods", set()) or set(), path))
    return out


def test_every_body_cap_matches_a_registered_route():
    routes = _sampled_routes()
    for methods, pattern, _cap in BODY_CAPS:
        assert any(
            (m & methods) and pattern.match(p) for m, p in routes
        ), f"BODY_CAPS pattern matches no registered route: {pattern.pattern}"


def test_body_size_middleware_is_mounted_inside_access_log():
    names = [
        getattr(m.kwargs.get("dispatch"), "__name__", getattr(m.cls, "__name__", ""))
        for m in app.user_middleware
    ]
    assert "body_size_middleware" in names, names
    # user_middleware is outermost-first: access_log must come BEFORE
    # body_size in this list (it wraps it), auth after.
    al, bs, au = (
        names.index("access_log_middleware"),
        names.index("body_size_middleware"),
        names.index("auth_middleware"),
    )
    assert al < bs < au, names
