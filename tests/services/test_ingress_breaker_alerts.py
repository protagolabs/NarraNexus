"""
@file_name: test_ingress_breaker_alerts.py
@author:
@date: 2026-08-25
@description: The owner-facing half of the ingress breaker.

This is the ONLY human-facing exit the ingress breaker has. The audit
plane is the evidence trail (after the fact); this is the alert (during).
The whole PR exists because an incident ran 70 hours with nobody knowing,
so a quota that silently drops notices past the third one reproduces that
problem at a per-session scale.

The quota was the one piece of new stateful, two-time-window logic in the
PR and it shipped with no tests at all — which is how "an over-quota trip
also burns its own dedup slot" survived review.
"""
from __future__ import annotations

import pytest

import xyz_agent_context.services.background_llm_alerts as alerts

pytestmark = pytest.mark.asyncio


class _Verdict:
    """Structural stand-in — ``services/`` must not import ``channel/``."""

    def __init__(self, session_key="agt|nm|!r|@p", reason="repeat_storm",
                 suppressed=0, window_count=20, dup_ratio=0.95, tier=1):
        self.session_key = session_key
        self.reason = reason
        self.suppressed = suppressed
        self.window_count = window_count
        self.dup_ratio = dup_ratio
        self.tier = tier
        self.cooldown_seconds = 300.0
        self.is_agent_peer = True

    def audit_details(self):
        return {"session_key": self.session_key, "tier": self.tier}


class _Db:
    async def get_one(self, table, filters):
        return {"created_by": "owner_1"}


@pytest.fixture(autouse=True)
def _wiring(monkeypatch):
    """Capture inbox writes and audit writes instead of hitting the DB."""
    sent, audits = [], []

    class _Inbox:
        def __init__(self, db):
            pass

        async def create_message(self, **kw):
            sent.append(kw)

    class _Auditor:
        def __init__(self, service):
            self.service = service

        async def error(self, payload):
            audits.append((self.service, payload))

    async def _fake_db():
        return _Db()

    monkeypatch.setattr(alerts, "InboxRepository", _Inbox)
    monkeypatch.setattr(alerts, "ServiceAuditor", _Auditor)
    monkeypatch.setattr(alerts, "get_db_client", _fake_db)
    alerts.reset_alert_state()
    yield sent, audits
    alerts.reset_alert_state()


async def _trip(agent_id="agt_1", **kw):
    await alerts.alert_ingress_breaker_tripped(
        agent_id=agent_id, db=_Db(), channel="NarraMessenger",
        verdict=_Verdict(**kw),
    )


# ── The quota's hard boundary: evidence is never rationed ─────────────

async def test_every_trip_is_audited_no_matter_the_quota(_wiring):
    sent, audits = _wiring
    for i in range(10):
        await _trip(session_key=f"agt_1|nm|!r{i}|@p")
    assert len(audits) == 10, "the evidence chain must be lossless"
    assert all(svc == "ingress_breaker" for svc, _ in audits)


# ── ...but the human channel is ─────────────────────────────────────────

async def test_detailed_notices_stop_at_the_quota(_wiring):
    sent, _ = _wiring
    for i in range(6):
        await _trip(session_key=f"agt_1|nm|!r{i}|@p")

    detailed = [m for m in sent if m["source"].type == "ingress_breaker"]
    assert len(detailed) == alerts.INGRESS_NOTICE_QUOTA_PER_AGENT


async def test_over_quota_produces_exactly_one_digest(_wiring):
    """Past the quota the owner used to receive NOTHING — no summary, no
    count, no pointer to the audit trail."""
    sent, _ = _wiring
    for i in range(8):
        await _trip(session_key=f"agt_1|nm|!r{i}|@p")

    digests = [m for m in sent if m["source"].type == "ingress_breaker_digest"]
    assert len(digests) == 1, "one digest per agent per window — not zero, not N"
    assert "audit" in digests[0]["content"].lower()


async def test_a_quota_suppressed_session_keeps_its_dedup_slot(_wiring):
    """Arming the per-session dedup slot for a notice that was never sent
    meant the session stayed silent for the rest of the window too."""
    sent, _ = _wiring
    for i in range(alerts.INGRESS_NOTICE_QUOTA_PER_AGENT):
        await _trip(session_key=f"agt_1|nm|!other{i}|@p")

    victim = "agt_1|nm|!victim|@p"
    await _trip(session_key=victim)  # suppressed by quota
    assert alerts._notify_cooldown.get(f"ingress:{victim}") is None, (
        "a session that never got a notice must stay eligible for one"
    )


async def test_quota_is_per_agent(_wiring):
    sent, _ = _wiring
    for i in range(5):
        await _trip(agent_id="agt_a", session_key=f"agt_a|nm|!r{i}|@p")
    before = len([m for m in sent if m["source"].type == "ingress_breaker"])

    await _trip(agent_id="agt_b", session_key="agt_b|nm|!r|@p")
    after = len([m for m in sent if m["source"].type == "ingress_breaker"])
    assert after == before + 1, "one noisy agent must not gag another"


# ── I8: the escalation notice must not shrink the incident ────────────

async def test_a_resumed_recital_notice_reports_what_the_pause_absorbed(_wiring):
    """The state machine records window_count=1 / ratio=1.0 for this
    transition — correctly, since the evidence IS a single fingerprint
    match. Rendering those numbers to a person produced "sent 1 messages
    that were 100% repeats of each other", which reads as an
    over-sensitive breaker while describing the opposite."""
    sent, _ = _wiring
    await _trip(reason="probe_repeated", window_count=1, dup_ratio=1.0,
                suppressed=52)

    body = sent[0]["content"]
    assert "52" in body, "the number that describes the incident is unused"
    assert "1 messages" not in body


async def test_an_ordinary_trip_still_reports_the_window(_wiring):
    sent, _ = _wiring
    await _trip(window_count=20, dup_ratio=0.95)
    assert "20 messages" in sent[0]["content"]
