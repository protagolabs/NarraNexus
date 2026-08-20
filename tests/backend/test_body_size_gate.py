"""
@file_name: test_body_size_gate.py
@date: 2026-08-20
@description: Drift pins for the body-size middleware (review #334 r4 I2 +
r5 I1/I2): the gate's existence depends on strings in two files staying
aligned, and neither a route move nor an unmounted middleware would fail
any other test.

- every BODY_CAPS pattern must match a route REGISTERED on the real app
  (paths sampled from the live route table, not hand-copied strings);
- every REGISTERED write route (POST/PUT/PATCH) must either be covered by
  a BODY_CAPS entry or sit in the explicit exemption list below — adding a
  write endpoint without deciding its size story is a CI failure, not a
  silent gap (the r3 lesson: a Pydantic body field means FastAPI buffers
  the whole payload BEFORE any dependency runs, so an uncapped body-field
  route has no memory bound at all);
- the middleware must actually be mounted on the app;
- the mount order must keep CORS outermost (a 401/413 without an ACAO
  header is invisible to a cross-origin caller), body_size inside
  access_log (its 413s belong in the access log) and outside auth.
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

# Write routes with NO BODY_CAPS entry, by ROUTE TEMPLATE (r.path verbatim —
# sampled paths would silently detach when a path-parameter name changes).
# Every entry is an explicit "no cap needed" decision; the groups state why.
# A new write endpoint must either get a BODY_CAPS line or be added here —
# either way the size story is decided in review, not defaulted.
_NO_BODY_CAP_EXEMPT = frozenset({
    # ── uploads with their OWN in-handler limits (declared + enforced at
    #    the route; adding a middleware cap would be a second spelling of
    #    the same number) ─────────────────────────────────────────────────
    "/api/agents/{agent_id}/attachments",      # settings-driven max_bytes check
    "/api/agents/{agent_id}/files",            # same upload plumbing as attachments
    "/api/teams/{team_id}/chat/attachments",   # same upload plumbing, team-scoped
    "/api/bundle/skills/archives/upload",      # MAX_BUNDLE_BYTES declared+streamed
    "/api/bundle/import/confirm",              # MAX_BUNDLE_BYTES declared+streamed
    "/api/bundle/import/preflight",            # MAX_BUNDLE_BYTES declared+streamed
    # ── small fixed-shape JSON bodies (config / action / auth payloads of
    #    scalar fields; no user-file-scale content travels through them).
    #    A platform-wide default cap for this class is a separate decision,
    #    tracked in body_size.py.md — exempting them here documents today's
    #    state, it does not bless it forever. ─────────────────────────────
    "/api/admin/gateway-key-misuse",
    "/api/admin/migrate-identity",
    "/api/admin/quota/init",
    "/api/admin/quota/topup",
    "/api/admin/reinstate",
    "/api/admin/suspend",
    "/api/admin/warn-user",
    "/api/agent-inbox/rooms/{room_id}/read",
    "/api/agent-inbox/{message_id}/read",
    "/api/agents/{agent_id}/artifacts/register",
    "/api/agents/{agent_id}/artifacts/url",
    "/api/agents/{agent_id}/artifacts/{artifact_id}",
    "/api/agents/{agent_id}/artifacts/{artifact_id}/embed-mode",
    "/api/agents/{agent_id}/artifacts/{artifact_id}/heal",
    "/api/agents/{agent_id}/artifacts/{artifact_id}/office-edit-commit",
    "/api/agents/{agent_id}/awareness",
    "/api/agents/{agent_id}/bus-failures/{message_id}/retry",
    "/api/agents/{agent_id}/channels/{channel}/credential",
    "/api/agents/{agent_id}/chat-history/by-instance",
    "/api/agents/{agent_id}/circuit-breaker/reset",
    "/api/agents/{agent_id}/jobs",
    "/api/agents/{agent_id}/jobs/search-keywords",
    "/api/agents/{agent_id}/jobs/search-semantic",
    "/api/agents/{agent_id}/jobs/{job_id}/cancel",
    "/api/agents/{agent_id}/jobs/{job_id}/pause",
    "/api/agents/{agent_id}/jobs/{job_id}/update",
    "/api/agents/{agent_id}/llm-config/{slot_name}",
    "/api/agents/{agent_id}/mcps",
    "/api/agents/{agent_id}/mcps/validate-all",
    "/api/agents/{agent_id}/mcps/{mcp_id}",
    "/api/agents/{agent_id}/mcps/{mcp_id}/validate",
    "/api/agents/{agent_id}/memory/retain",
    "/api/agents/{agent_id}/narratives",
    "/api/agents/{agent_id}/narratives/{narrative_id}/switch",
    "/api/agents/{agent_id}/profile/update",
    "/api/agents/{agent_id}/social-network/contact",
    "/api/agents/{agent_id}/social-network/create-agent",
    "/api/agents/{agent_id}/social-network/delete-entity",
    "/api/agents/{agent_id}/social-network/extract",
    "/api/agents/{agent_id}/social-network/merge",
    "/api/agents/{agent_id}/social-network/recall",
    "/api/agents/{agent_id}/social-network/stats",
    "/api/analytics/events",
    "/api/arena/provision",
    "/api/auth/agents",
    "/api/auth/agents/{agent_id}",
    "/api/auth/create-user",
    "/api/auth/funnel-report",
    "/api/auth/login",
    "/api/auth/netmind-login",
    "/api/auth/onboarding",
    "/api/auth/settings/analytics",
    "/api/auth/settings/reply-language",
    "/api/auth/settings/telemetry",
    "/api/auth/signup",
    "/api/auth/signup/send-code",
    "/api/auth/timezone",
    "/api/billing/cancel",
    "/api/billing/reactivate",
    "/api/billing/recharge",
    "/api/billing/subscribe",
    "/api/bundle/export",
    "/api/bundle/export/preview/artifacts",
    "/api/bundle/export/preview/bus-channels",
    "/api/bundle/export/preview/mcps",
    "/api/bundle/import/from-url",
    "/api/dashboard/jobs/{job_id}/pause",
    "/api/dashboard/jobs/{job_id}/resume",
    "/api/dashboard/jobs/{job_id}/retry",
    "/api/dashboard/jobs/{job_id}/schedule",
    "/api/discord/bind",
    "/api/discord/set-active",
    "/api/discord/test",
    "/api/discord/unbind",
    "/api/feedback",
    "/api/home-assistant/binding",
    "/api/home-assistant/test",
    "/api/home-assistant/verify",
    "/api/jobs/complex",
    "/api/jobs/{job_id}",
    "/api/jobs/{job_id}/cancel",
    "/api/jobs/{job_id}/pause",
    "/api/lark/auth/complete",
    "/api/lark/auth/login",
    "/api/lark/bind",
    "/api/lark/set-active",
    "/api/lark/test",
    "/api/lark/unbind",
    "/api/marketplace/skills/publish",
    "/api/marketplace/skills/{skill_id}/install",
    "/api/marketplace/teams/templates",
    "/api/marketplace/teams/templates/{template_id}/install-preflight",
    "/api/migrate/apply",
    "/api/migrate/scan",
    "/api/narramessenger/bind",
    "/api/narramessenger/prewarm",
    "/api/narramessenger/unbind",
    "/api/notices/{message_id}/read",
    "/api/notifications/read-all",
    "/api/notifications/{notification_id}/read",
    "/api/providers",
    "/api/providers/agent-framework",
    "/api/providers/onboard",
    "/api/providers/slots/{slot_name}",
    "/api/providers/sync-defaults",
    "/api/providers/test-config",
    "/api/providers/use-subscription",
    "/api/providers/{provider_id}/models",
    "/api/providers/{provider_id}/test",
    "/api/runs/{run_id}/cancel",
    "/api/skills/install",
    "/api/skills/{skill_name}/disable",
    "/api/skills/{skill_name}/enable",
    "/api/skills/{skill_name}/env",
    "/api/skills/{skill_name}/study",
    "/api/slack/bind",
    "/api/slack/set-active",
    "/api/slack/test",
    "/api/slack/unbind",
    "/api/teams",
    "/api/teams/{team_id}",
    "/api/teams/{team_id}/artifacts/{artifact_id}/view-token",
    "/api/teams/{team_id}/bulletin",
    "/api/teams/{team_id}/bulletin/{entry_id}",
    "/api/teams/{team_id}/chat/messages",
    "/api/teams/{team_id}/members",
    "/api/teams/{team_id}/patrol",
    "/api/teams/{team_id}/work-items/{item_id}/resume",
    "/api/telegram/bind",
    "/api/telegram/set-active",
    "/api/telegram/test",
    "/api/telegram/unbind",
    "/api/wechat/qrcode/poll",
    "/api/wechat/qrcode/start",
    "/api/wechat/set-active",
    "/api/wechat/unbind",
})


def _sampled(path: str) -> str:
    for k, v in _SAMPLE.items():
        path = path.replace(k, v)
    return path


def _sampled_routes():
    out = []
    for r in app.routes:
        path = getattr(r, "path", "")
        out.append((getattr(r, "methods", set()) or set(), _sampled(path)))
    return out


def test_every_body_cap_matches_a_registered_route():
    routes = _sampled_routes()
    for methods, pattern, _cap in BODY_CAPS:
        assert any(
            (m & methods) and pattern.match(p) for m, p in routes
        ), f"BODY_CAPS pattern matches no registered route: {pattern.pattern}"


def test_every_write_route_has_a_cap_or_an_exemption():
    """The reverse drift pin (review #334 r5 I2): 'a new write entrance must
    add a BODY_CAPS line' lived only in body_size.py.md — this makes it
    executable. Uncovered AND unexempted write routes fail here, turning
    'forgot to think about the size story' into an explicit review decision."""
    write = {"POST", "PUT", "PATCH"}
    missing = []
    stale = set(_NO_BODY_CAP_EXEMPT)
    for r in app.routes:
        methods = (getattr(r, "methods", set()) or set()) & write
        if not methods:
            continue  # GET/WS/Mount rows carry no request body to cap
        template = getattr(r, "path", "")
        stale.discard(template)
        sampled = _sampled(template)
        capped = any(
            (m & methods) and pat.match(sampled) for m, pat, _cap in BODY_CAPS
        )
        if not capped and template not in _NO_BODY_CAP_EXEMPT:
            missing.append(f"{sorted(methods)} {template}")
    assert not missing, (
        "write route(s) with neither a BODY_CAPS entry nor an explicit "
        f"exemption — decide their size story: {missing}"
    )
    # an exemption whose route disappeared is dead weight — prune it
    assert not stale, f"exemptions matching no registered route: {sorted(stale)}"


def test_middleware_order_cors_outermost_body_size_inside_access_log():
    names = [
        getattr(m.kwargs.get("dispatch"), "__name__", getattr(m.cls, "__name__", ""))
        for m in app.user_middleware
    ]
    assert "body_size_middleware" in names, names
    # user_middleware is outermost-first. CORS must be outermost of the
    # four (constraint 2 in main.py — the one that WAS broken once: an
    # inner CORS never runs on early 401/413 returns, so cross-origin
    # callers get responses without ACAO and see only an opaque error).
    # Relative order only — a new outermost layer (e.g. tracing) is legal.
    cors, al, bs, au = (
        names.index("CORSMiddleware"),
        names.index("access_log_middleware"),
        names.index("body_size_middleware"),
        names.index("auth_middleware"),
    )
    assert cors < al < bs < au, names
