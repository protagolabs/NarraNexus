"""
@file_name: test_approvals.py
@author:
@date: 2026-09-22
@description: Tests for independent privileged-capability approvals.

`decide()` can already answer "ask", but until a human can act on that the
answer is a dead end: the agent reports NEEDS_HUMAN and nothing the user can
do changes it. This is the part that turns an `ask` into a pending request,
lets the user answer it, and applies the answer to the live session.

The properties that matter are about not training people to click yes:

* a pending request names the exact origin and capability, never "a website";
* answering one request must not answer another;
* "for this conversation" must not silently become "forever";
* an approval can never override a configured deny.
"""
from __future__ import annotations

import pytest

from narranexus.platform.browser._browser_impl.approvals import (
    ApprovalRegistry,
    PendingApproval,
)
from narranexus.platform.browser._browser_impl.policy import (
    BrowserPolicy,
    OriginPolicy,
    decide,
)


def registry() -> ApprovalRegistry:
    return ApprovalRegistry()


# ── raising a request ────────────────────────────────────────────────────────


def test_requesting_returns_an_id_and_records_the_details():
    reg = registry()
    pending = reg.request(
        agent_id="a1", origin="https://x.example", capability="downloads",
        turn_id="t1", thread_id="th1",
    )
    assert isinstance(pending, PendingApproval)
    assert pending.origin == "https://x.example"
    assert pending.capability == "downloads"
    assert pending.id


def test_pending_lists_only_that_agents_requests():
    reg = registry()
    reg.request(agent_id="a1", origin="https://x.example", capability="downloads",
                turn_id="t", thread_id="th")
    reg.request(agent_id="a2", origin="https://y.example", capability="downloads",
                turn_id="t", thread_id="th")

    assert [p.origin for p in reg.pending("a1")] == ["https://x.example"]


def test_asking_twice_for_the_same_thing_reuses_the_request():
    """Repeated requests for one capability must not stack identical prompts."""
    reg = registry()
    first = reg.request(agent_id="a1", origin="https://x.example", capability="downloads",
                        turn_id="t", thread_id="th")
    second = reg.request(agent_id="a1", origin="https://x.example", capability="downloads",
                         turn_id="t", thread_id="th")

    assert first.id == second.id
    assert len(reg.pending("a1")) == 1


def test_different_capabilities_at_one_origin_are_separate_requests():
    """Download and upload capabilities have independent decisions."""
    reg = registry()
    reg.request(agent_id="a1", origin="https://x.example", capability="downloads",
                turn_id="t", thread_id="th")
    reg.request(agent_id="a1", origin="https://x.example", capability="uploads",
                turn_id="t", thread_id="th")

    assert len(reg.pending("a1")) == 2


# ── answering ────────────────────────────────────────────────────────────────


def test_allowing_for_the_thread_grants_it_on_the_live_policy():
    reg = registry()
    policy = BrowserPolicy()
    p = reg.request(agent_id="a1", origin="https://x.example", capability="downloads",
                    turn_id="t1", thread_id="th1")

    ok = reg.resolve(p.id, decision="allow", lifetime="thread", policy=policy)

    assert ok is True
    verdict = decide(policy, url="https://x.example/page", capability="downloads",
                     turn_id="t9", thread_id="th1")
    assert verdict.verdict == "allow"


def test_allowing_for_one_turn_does_not_outlive_it():
    reg = registry()
    policy = BrowserPolicy(default_origin_policy=OriginPolicy(downloads="ask"))
    p = reg.request(agent_id="a1", origin="https://x.example", capability="downloads",
                    turn_id="t1", thread_id="th1")

    reg.resolve(p.id, decision="allow", lifetime="turn", policy=policy)

    assert decide(policy, url="https://x.example/", capability="downloads",
                  turn_id="t1", thread_id="th1").verdict == "allow"
    assert decide(policy, url="https://x.example/", capability="downloads",
                  turn_id="t2", thread_id="th1").verdict == "ask"


def test_allowing_always_is_written_into_the_policy_itself():
    """'Always' has to survive a restart, so it becomes configuration rather
    than a session grant."""
    reg = registry()
    policy = BrowserPolicy()
    p = reg.request(agent_id="a1", origin="https://x.example", capability="downloads",
                    turn_id="t1", thread_id="th1")

    updated = reg.resolve(p.id, decision="allow", lifetime="always", policy=policy)

    assert updated is not False
    assert policy.origins["https://x.example"].downloads == "allow"


def test_denying_records_a_deny_so_the_agent_stops_asking():
    reg = registry()
    policy = BrowserPolicy()
    p = reg.request(agent_id="a1", origin="https://bad.example", capability="downloads",
                    turn_id="t1", thread_id="th1")

    reg.resolve(p.id, decision="deny", lifetime="always", policy=policy)

    assert decide(policy, url="https://bad.example/", capability="downloads",
                  turn_id="t1", thread_id="th1").verdict == "deny"


def test_resolving_removes_it_from_pending():
    reg = registry()
    policy = BrowserPolicy()
    p = reg.request(agent_id="a1", origin="https://x.example", capability="downloads",
                    turn_id="t", thread_id="th")

    reg.resolve(p.id, decision="allow", lifetime="thread", policy=policy)

    assert reg.pending("a1") == []


def test_resolving_one_request_does_not_resolve_another():
    reg = registry()
    policy = BrowserPolicy()
    a = reg.request(agent_id="a1", origin="https://a.example", capability="downloads",
                    turn_id="t", thread_id="th")
    reg.request(agent_id="a1", origin="https://b.example", capability="downloads",
                turn_id="t", thread_id="th")

    reg.resolve(a.id, decision="allow", lifetime="thread", policy=policy)

    assert [p.origin for p in reg.pending("a1")] == ["https://b.example"]


def test_an_unknown_request_id_is_refused_not_silently_accepted():
    reg = registry()
    assert reg.resolve("nope", decision="allow", lifetime="thread",
                       policy=BrowserPolicy()) is False


def test_an_approval_cannot_override_a_configured_deny():
    """The prompt must not be a route around a ban the user already set."""
    reg = registry()
    policy = BrowserPolicy(origins={"https://bad.example": OriginPolicy(downloads="deny")})
    p = reg.request(agent_id="a1", origin="https://bad.example", capability="downloads",
                    turn_id="t1", thread_id="th1")

    reg.resolve(p.id, decision="allow", lifetime="thread", policy=policy)

    assert decide(policy, url="https://bad.example/", capability="downloads",
                  turn_id="t1", thread_id="th1").verdict == "deny"


def test_full_cdp_access_stays_denied_when_nobody_configured_it():
    """Design §6: the one capability with no ceiling is configured
    deliberately, not clicked through. Raising the prompt at all is refused
    (see the two tests below); this pins that the default stands."""
    policy = BrowserPolicy()
    assert decide(policy, url="https://x.example/", capability="full_cdp_access",
                  turn_id="t1", thread_id="th1").verdict == "deny"


def test_pending_requests_are_serialisable_for_the_api():
    reg = registry()
    p = reg.request(agent_id="a1", origin="https://x.example", capability="downloads",
                    turn_id="t", thread_id="th")
    d = p.to_dict()
    assert d["origin"] == "https://x.example"
    assert d["capability"] == "downloads"
    assert set(d) >= {"id", "origin", "capability", "agent_id", "requested_at"}


@pytest.mark.parametrize("lifetime", ["turn", "thread", "always"])
def test_every_documented_lifetime_is_accepted(lifetime):
    reg = registry()
    policy = BrowserPolicy()
    p = reg.request(agent_id="a1", origin="https://x.example", capability="downloads",
                    turn_id="t1", thread_id="th1")
    assert reg.resolve(p.id, decision="allow", lifetime=lifetime, policy=policy) is not False


def test_an_unknown_lifetime_is_refused():
    reg = registry()
    policy = BrowserPolicy()
    p = reg.request(agent_id="a1", origin="https://x.example", capability="downloads",
                    turn_id="t1", thread_id="th1")
    with pytest.raises(ValueError, match="forever"):
        reg.resolve(p.id, decision="allow", lifetime="forever", policy=policy)


def test_a_prompt_for_full_cdp_access_is_refused_at_the_source():
    """`resolve(..., lifetime="always")` writes configuration, so blocking
    full_cdp_access only on the session-grant path left a way through: raise
    the prompt, answer it with "always", and the ungated capability is
    configured by a click. The prompt must not exist in the first place."""
    reg = registry()
    with pytest.raises(ValueError, match="full_cdp_access"):
        reg.request(agent_id="a1", origin="https://x.example",
                    capability="full_cdp_access", turn_id="t", thread_id="th")


def test_always_cannot_grant_full_cdp_access_even_if_a_request_is_forged():
    """Defence in depth: even handed a request for it, resolving must not
    write it as configuration."""
    reg = registry()
    policy = BrowserPolicy()
    forged = PendingApproval(id="appr_forged", agent_id="a1",
                             origin="https://x.example", capability="full_cdp_access",
                             turn_id="t", thread_id="th")
    reg._by_id[forged.id] = forged  # noqa: SLF001

    reg.resolve(forged.id, decision="allow", lifetime="always", policy=policy)

    assert decide(policy, url="https://x.example/", capability="full_cdp_access",
                  turn_id="t", thread_id="th").verdict == "deny"
