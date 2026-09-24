"""
@file_name: test_policy.py
@author:
@date: 2026-09-22
@description: Tests for the per-origin browser permission model (design §6).

Independent privileged capabilities retain origin matching and scoped answers.
Ordinary website browsing is outside this policy model.
"""
from __future__ import annotations

import pytest

from narranexus.platform.browser._browser_impl.policy import (
    BrowserPolicy,
    OriginPolicy,
    decide,
    origin_of,
)


def policy(**kw) -> BrowserPolicy:
    """Explicit approval policy for origin matching and scoped-grant tests."""
    return BrowserPolicy(
        default_origin_policy=kw.pop("default", OriginPolicy(downloads="ask")),
        origins=kw.pop("origins", {}),
        allow_history_access=kw.pop("allow_history_access", False),
    )


# ── origin_of ────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "url,expected",
    [
        ("https://github.com/features", "https://github.com"),
        ("https://github.com:443/x", "https://github.com"),
        ("http://example.com:8080/a/b", "http://example.com:8080"),
        ("https://GitHub.COM/X", "https://github.com"),
        ("https://sub.github.com/", "https://sub.github.com"),
    ],
)
def test_origin_of_normalises(url, expected):
    assert origin_of(url) == expected


@pytest.mark.parametrize(
    "url", ["file:///etc/passwd", "javascript:alert(1)", "data:text/html,x", "", "not a url"]
)
def test_origin_of_rejects_non_http_schemes(url):
    """file: and javascript: are how a navigation becomes a local-file read or
    a script injection; they never resolve to an origin we can grant."""
    assert origin_of(url) is None


# ── defaults are safe ────────────────────────────────────────────────────────


@pytest.mark.parametrize("document", [None, {}, {"default_origin_policy": {}, "origins": {}}])
def test_file_permission_defaults_are_consistent_for_new_and_existing_agents(document):
    p = BrowserPolicy() if document is None else BrowserPolicy.from_dict(document)
    d = decide(p, url="https://github.com/", capability="downloads")
    assert d.verdict == "ask"
    assert d.matched == "default"


@pytest.mark.parametrize("verdict", ["ask", "deny"])
def test_explicit_file_default_is_preserved(verdict):
    p = policy(default=OriginPolicy(downloads=verdict))
    assert decide(p, url="https://github.com/", capability="downloads").verdict == verdict


def test_full_cdp_access_is_denied_by_default_not_asked():
    """Design §6: raw CDP is the whole browser. It is not something to put
    behind a routine yes/no prompt the user will click through."""
    d = decide(policy(), url="https://github.com/", capability="full_cdp_access")
    assert d.verdict == "deny"


@pytest.mark.parametrize("cap", ["downloads", "uploads"])
def test_file_movement_asks_by_default(cap):
    assert decide(policy(), url="https://x.example/", capability=cap).verdict == "ask"


def test_non_http_url_is_denied_for_every_capability():
    for cap in ("downloads", "uploads", "full_cdp_access"):
        d = decide(policy(), url="file:///etc/passwd", capability=cap)
        assert d.verdict == "deny"
        assert d.reason == "unsupported-scheme"


# ── explicit origin beats default ────────────────────────────────────────────


def test_exact_origin_overrides_default():
    p = policy(
        default=OriginPolicy(downloads="deny"),
        origins={"https://github.com": OriginPolicy(downloads="allow")},
    )
    assert decide(p, url="https://github.com/x", capability="downloads").verdict == "allow"
    assert decide(p, url="https://other.com/x", capability="downloads").verdict == "deny"


def test_origin_match_is_scheme_sensitive():
    """An http:// page is not the https:// origin the user approved."""
    p = policy(origins={"https://bank.example": OriginPolicy(downloads="allow")})
    assert decide(p, url="http://bank.example/", capability="downloads").verdict != "allow"


def test_wildcard_host_matches_subdomains():
    p = policy(origins={"https://*.github.com": OriginPolicy(downloads="allow")})
    assert decide(p, url="https://gist.github.com/x", capability="downloads").verdict == "allow"


def test_wildcard_does_not_match_the_bare_domain():
    """`*.github.com` and `github.com` are different grants; conflating them
    is how a grant silently widens."""
    p = policy(origins={"https://*.github.com": OriginPolicy(downloads="allow")})
    assert decide(p, url="https://github.com/", capability="downloads").verdict != "allow"


def test_wildcard_does_not_match_a_lookalike_suffix():
    """`evil-github.com` must not match `*.github.com`."""
    p = policy(origins={"https://*.github.com": OriginPolicy(downloads="allow")})
    assert decide(p, url="https://evil-github.com/", capability="downloads").verdict != "allow"


def test_exact_match_wins_over_wildcard():
    p = policy(
        origins={
            "https://*.github.com": OriginPolicy(downloads="allow"),
            "https://gist.github.com": OriginPolicy(downloads="deny"),
        }
    )
    assert decide(p, url="https://gist.github.com/", capability="downloads").verdict == "deny"


# ── per-capability independence ──────────────────────────────────────────────


def test_allowing_downloads_does_not_allow_uploads():
    """Receiving a file is not permission to send one."""
    p = policy(origins={"https://x.example": OriginPolicy(downloads="allow")})
    assert decide(p, url="https://x.example/", capability="downloads").verdict == "allow"
    assert decide(p, url="https://x.example/", capability="uploads").verdict == "ask"


def test_origin_entry_inherits_unset_fields_from_the_default():
    p = policy(
        default=OriginPolicy(downloads="allow"),
        origins={"https://x.example": OriginPolicy(uploads="allow")},
    )
    d = decide(p, url="https://x.example/", capability="downloads")
    assert d.verdict == "allow"
    assert d.matched == "https://x.example"


def test_deny_on_the_origin_is_not_overridden_by_an_allowing_default():
    p = policy(
        default=OriginPolicy(downloads="allow"),
        origins={"https://bad.example": OriginPolicy(downloads="deny")},
    )
    assert decide(p, url="https://bad.example/", capability="downloads").verdict == "deny"


# ── grants and their lifetime ────────────────────────────────────────────────


def test_turn_grant_applies_within_the_same_turn():
    p = policy()
    p.grant(origin="https://x.example", capability="downloads", lifetime="turn",
            turn_id="t1", thread_id="th1")
    d = decide(p, url="https://x.example/", capability="downloads", turn_id="t1", thread_id="th1")
    assert d.verdict == "allow"
    assert d.reason == "granted"


def test_turn_grant_does_not_survive_into_the_next_turn():
    p = policy()
    p.grant(origin="https://x.example", capability="downloads", lifetime="turn",
            turn_id="t1", thread_id="th1")
    d = decide(p, url="https://x.example/", capability="downloads", turn_id="t2", thread_id="th1")
    assert d.verdict == "ask"


def test_thread_grant_survives_across_turns_in_the_same_thread():
    p = policy()
    p.grant(origin="https://x.example", capability="downloads", lifetime="thread",
            turn_id="t1", thread_id="th1")
    d = decide(p, url="https://x.example/", capability="downloads", turn_id="t9", thread_id="th1")
    assert d.verdict == "allow"


def test_thread_grant_does_not_leak_into_another_thread():
    p = policy()
    p.grant(origin="https://x.example", capability="downloads", lifetime="thread",
            turn_id="t1", thread_id="th1")
    d = decide(p, url="https://x.example/", capability="downloads", turn_id="t1", thread_id="th2")
    assert d.verdict == "ask"


def test_a_grant_cannot_override_an_explicit_deny():
    """Approving a prompt must not be a way around a configured ban — that is
    exactly the escalation a malicious page would aim for."""
    p = policy(origins={"https://bad.example": OriginPolicy(downloads="deny")})
    p.grant(origin="https://bad.example", capability="downloads", lifetime="thread",
            turn_id="t1", thread_id="th1")
    d = decide(p, url="https://bad.example/", capability="downloads", turn_id="t1", thread_id="th1")
    assert d.verdict == "deny"


def test_grants_are_per_capability():
    p = policy()
    p.grant(origin="https://x.example", capability="downloads", lifetime="thread",
            turn_id="t1", thread_id="th1")
    d = decide(p, url="https://x.example/", capability="uploads", turn_id="t1", thread_id="th1")
    assert d.verdict == "ask"


def test_full_cdp_grant_is_refused_even_when_asked_for():
    """`full_cdp_access` is deny-by-default and not grantable through the
    routine approval path; it has to be configured deliberately."""
    p = policy()
    p.grant(origin="https://x.example", capability="full_cdp_access", lifetime="thread",
            turn_id="t1", thread_id="th1")
    d = decide(p, url="https://x.example/", capability="full_cdp_access",
               turn_id="t1", thread_id="th1")
    assert d.verdict == "deny"


# ── serialisation round trip ─────────────────────────────────────────────────


def test_policy_round_trips_through_dict():
    p = policy(
        default=OriginPolicy(downloads="ask", uploads="deny"),
        origins={"https://x.example": OriginPolicy(downloads="allow", uploads="deny")},
        allow_history_access=True,
    )
    again = BrowserPolicy.from_dict(p.to_dict())
    assert again.to_dict() == p.to_dict()
    assert again.allow_history_access is True
    assert decide(again, url="https://x.example/", capability="downloads").verdict == "allow"


def test_from_dict_ignores_unknown_fields_rather_than_exploding():
    """Config written by a newer build must not brick an older one."""
    p = BrowserPolicy.from_dict(
        {
            "allow_history_access": False,
            "default_origin_policy": {"downloads": "allow", "future_thing": "x"},
            "origins": {"https://a.example": {"downloads": "deny", "another": 1}},
            "unknown_top_level": True,
        }
    )
    assert decide(p, url="https://a.example/", capability="downloads").verdict == "deny"
    assert decide(p, url="https://b.example/", capability="downloads").verdict == "allow"


def test_from_dict_rejects_an_invalid_verdict_value():
    """A typo'd verdict must fail loudly at load, not silently become 'ask'."""
    with pytest.raises(ValueError, match="maybe"):
        BrowserPolicy.from_dict({"default_origin_policy": {"downloads": "maybe"}})


# ── grants must survive the trip between processes ──────────────────────────


def test_grants_round_trip_through_the_document():
    """The session runs in the MCP host process and the approval is applied in
    the backend process. A grant kept only in memory is invisible to the side
    that has to honour it — the user clicks allow and the agent stays refused
    (observed live 2026-09-22)."""
    p = policy()
    p.grant(origin="https://x.example", capability="downloads", lifetime="thread",
            turn_id="t1", thread_id="th1")

    reloaded = BrowserPolicy.from_dict(p.to_dict())

    assert decide(reloaded, url="https://x.example/", capability="downloads",
                  turn_id="t9", thread_id="th1").verdict == "allow"


def test_a_turn_grant_round_trips_with_its_scope_intact():
    p = policy()
    p.grant(origin="https://x.example", capability="downloads", lifetime="turn",
            turn_id="t1", thread_id="th1")

    reloaded = BrowserPolicy.from_dict(p.to_dict())

    assert decide(reloaded, url="https://x.example/", capability="downloads",
                  turn_id="t1", thread_id="th1").verdict == "allow"
    assert decide(reloaded, url="https://x.example/", capability="downloads",
                  turn_id="t2", thread_id="th1").verdict == "ask"


def test_a_document_without_grants_still_loads():
    """Older rows, written before grants were persisted, must not break."""
    p = BrowserPolicy.from_dict({"origins": {}, "default_origin_policy": {}})
    assert decide(p, url="https://x.example/", capability="downloads").verdict == "ask"
