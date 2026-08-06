"""
@file_name: test_voice_delivery_bridge.py
@date: 2026-08-06
@description: VoiceDeliveryBridge — live m.text lifecycle + TTS sanitizer.

Contract source: Hybrid handoff section 6 (base live event, m.replace
cumulative updates, final edit removes the live marker) and section 7
(spoken output discipline).

Locks:
- First playable text -> base m.text WITH org.matrix.msc4357.live; no
  waiting for the full answer.
- Updates -> fresh events relating via m.replace to the base, cumulative
  sanitized text in BOTH body ("* " fallback prefix) and m.new_content,
  live marker in BOTH places.
- close() -> final edit WITHOUT the live marker anywhere; (text, True).
- Multiple speak calls concatenate into one cumulative stream.
- Cadence: no sentence boundary and interval not elapsed -> buffered.
- Split markdown across deltas never leaks marker chars.
- No deltas -> close() sends nothing, returns (None, True).
- Sender failure (base or final) -> close() reports finalized_ok=False
  so the trigger falls back to the plain delivery path.
- sanitize_for_tts strips markdown/emoji/code/URLs and collapses space.
"""
from __future__ import annotations

import pytest

from xyz_agent_context.module.narramessenger_module._voice_delivery import (
    LIVE_MARKER_KEY,
    VoiceDeliveryBridge,
    sanitize_for_tts,
)


# ── sanitizer ───────────────────────────────────────────────────────────


def test_sanitize_strips_markdown_and_emoji():
    raw = "# Hi\n**Sunny** today 😀, `25` degrees.\n- wind low\n1. calm"
    out = sanitize_for_tts(raw)
    assert out == "Hi Sunny today , 25 degrees. wind low calm"


def test_sanitize_removes_urls_and_code_fences():
    raw = "See [forecast](https://x.example/f) or https://y.example/z\n```py\nprint(1)\n```done"
    out = sanitize_for_tts(raw)
    assert "http" not in out
    assert "print" not in out
    assert "forecast" in out and "done" in out


# ── bridge ──────────────────────────────────────────────────────────────


class Recorder:
    """Failure injection is by ATTEMPT number (failed attempts count too),
    so retry semantics are testable: fail_on={1} fails only the second
    attempt, whatever it is."""

    def __init__(self, fail_on: set[int] | None = None):
        self.sent: list[dict] = []
        self.attempts = 0
        self._fail_on = fail_on or set()

    async def __call__(self, content: dict) -> str:
        attempt = self.attempts
        self.attempts += 1
        if attempt in self._fail_on:
            raise RuntimeError("send failed")
        self.sent.append(content)
        return f"$ev{len(self.sent) - 1}"


class FakeClock:
    def __init__(self):
        self.t = 0.0

    def __call__(self) -> float:
        return self.t


def _bridge(recorder, clock=None):
    return VoiceDeliveryBridge(
        send=recorder, clock=clock or FakeClock(), flush_interval_s=0.4
    )


@pytest.mark.asyncio
async def test_first_sentence_sends_live_base():
    rec = Recorder()
    bridge = _bridge(rec)
    await bridge.on_reply_delta(call_id="c1", delta="The weather is sunny.")
    assert len(rec.sent) == 1
    base = rec.sent[0]
    assert base["msgtype"] == "m.text"
    assert base["body"] == "The weather is sunny."
    assert base[LIVE_MARKER_KEY] == {}
    assert "m.relates_to" not in base


@pytest.mark.asyncio
async def test_update_relates_to_base_with_cumulative_text():
    rec = Recorder()
    bridge = _bridge(rec)
    await bridge.on_reply_delta(call_id="c1", delta="It is sunny.")
    await bridge.on_reply_delta(call_id="c1", delta=" Twenty five degrees.")
    assert len(rec.sent) == 2
    edit = rec.sent[1]
    assert edit["m.relates_to"] == {"rel_type": "m.replace", "event_id": "$ev0"}
    assert edit["body"] == "* It is sunny. Twenty five degrees."
    assert edit["m.new_content"]["body"] == "It is sunny. Twenty five degrees."
    assert edit[LIVE_MARKER_KEY] == {}
    assert edit["m.new_content"][LIVE_MARKER_KEY] == {}


@pytest.mark.asyncio
async def test_close_removes_live_marker_everywhere():
    rec = Recorder()
    bridge = _bridge(rec)
    await bridge.on_reply_delta(call_id="c1", delta="Sunny.")
    text, ok = await bridge.close()
    assert ok is True and text == "Sunny."
    final = rec.sent[-1]
    assert LIVE_MARKER_KEY not in final
    assert LIVE_MARKER_KEY not in final["m.new_content"]
    assert final["m.new_content"]["body"] == "Sunny."


@pytest.mark.asyncio
async def test_multiple_speak_calls_concatenate():
    rec = Recorder()
    bridge = _bridge(rec)
    await bridge.on_reply_delta(call_id="c1", delta="I am checking the weather now.")
    await bridge.on_reply_delta(call_id="c2", delta="It is sunny today.")
    text, ok = await bridge.close()
    assert ok
    assert text == "I am checking the weather now. It is sunny today."


@pytest.mark.asyncio
async def test_cadence_buffers_until_interval_or_boundary():
    rec = Recorder()
    clock = FakeClock()
    bridge = _bridge(rec, clock)
    await bridge.on_reply_delta(call_id="c1", delta="It is")
    assert rec.sent == []  # no boundary, interval not elapsed
    clock.t = 1.0
    await bridge.on_reply_delta(call_id="c1", delta=" sunny")
    assert len(rec.sent) == 1  # interval elapsed forces the flush


@pytest.mark.asyncio
async def test_split_markdown_markers_never_leak():
    rec = Recorder()
    bridge = _bridge(rec)
    await bridge.on_reply_delta(call_id="c1", delta="**Sun")
    await bridge.on_reply_delta(call_id="c1", delta="ny** today.")
    text, _ = await bridge.close()
    assert text == "Sunny today."
    for content in rec.sent:
        assert "*" not in content.get("m.new_content", content)["body"]


@pytest.mark.asyncio
async def test_no_deltas_close_is_silent():
    rec = Recorder()
    bridge = _bridge(rec)
    text, ok = await bridge.close()
    assert text is None and ok is True
    assert rec.sent == []


@pytest.mark.asyncio
async def test_base_send_failure_recovers_at_close_when_possible():
    """A failed live base doesn't doom the turn: close() retries a
    marker-free send, which doubles as the delivery."""
    rec = Recorder(fail_on={0})
    bridge = _bridge(rec)
    await bridge.on_reply_delta(call_id="c1", delta="Sunny.")  # must not raise
    text, ok = await bridge.close()
    assert text == "Sunny." and ok is True
    assert LIVE_MARKER_KEY not in rec.sent[-1]


@pytest.mark.asyncio
async def test_total_send_failure_reports_not_finalized():
    rec = Recorder(fail_on={0, 1})
    bridge = _bridge(rec)
    await bridge.on_reply_delta(call_id="c1", delta="Sunny.")
    text, ok = await bridge.close()
    assert text == "Sunny." and ok is False
    assert rec.sent == []


@pytest.mark.asyncio
async def test_final_edit_failure_reports_not_finalized():
    rec = Recorder(fail_on={1})
    bridge = _bridge(rec)
    await bridge.on_reply_delta(call_id="c1", delta="Sunny.")
    text, ok = await bridge.close()
    assert text == "Sunny." and ok is False


@pytest.mark.asyncio
async def test_authoritative_segment_text_replaces_deltas():
    """PROGRESS carries the full speak text; it corrects the delta view."""
    rec = Recorder()
    bridge = _bridge(rec)
    await bridge.on_reply_delta(call_id="c1", delta="Sunny tod")
    bridge.on_segment_text(call_id="c1", text="Sunny today, twenty five degrees.")
    text, ok = await bridge.close()
    assert ok
    assert text == "Sunny today, twenty five degrees."


@pytest.mark.asyncio
async def test_bridge_exposes_observability_stamps():
    """Handoff section 9: first_model_token / first_matrix_live_reply_sent /
    matrix_live_reply_finalized need trigger-side stamps."""
    rec = Recorder()
    clock = FakeClock()
    bridge = _bridge(rec, clock)
    assert bridge.first_delta_at is None and bridge.first_sent_at is None

    clock.t = 1.0
    await bridge.on_reply_delta(call_id="c1", delta="Sunny.")
    assert bridge.first_delta_at == 1.0
    assert bridge.first_sent_at == 1.0  # boundary flush sent immediately

    clock.t = 2.5
    await bridge.close()
    assert bridge.finalized_at == 2.5


@pytest.mark.asyncio
async def test_no_delta_multi_segment_authoritative_texts_all_survive():
    """Review finding #1: provider without arg deltas delivers speak only
    via PROGRESS. Segment boundaries must close on call_id change or every
    segment except the last is silently lost."""
    rec = Recorder()
    bridge = _bridge(rec)
    bridge.on_segment_text(call_id="c1", text="I am checking the weather now.")
    bridge.on_segment_text(call_id="c2", text="It is sunny, twenty five degrees.")
    text, ok = await bridge.close()
    assert ok
    assert text == "I am checking the weather now. It is sunny, twenty five degrees."


@pytest.mark.asyncio
async def test_close_recovers_live_state_after_mid_stream_failure():
    """Review finding #10: a failed mid-stream edit must not leave the base
    event permanently live when the final edit CAN still succeed."""
    rec = Recorder(fail_on={1})
    clock = FakeClock()
    bridge = _bridge(rec, clock)
    await bridge.on_reply_delta(call_id="c1", delta="First sentence.")  # idx0 base ok
    clock.t = 1.0
    await bridge.on_reply_delta(call_id="c1", delta=" Second one.")  # idx1 fails -> broken
    text, ok = await bridge.close()  # idx2: final edit retry succeeds
    assert text == "First sentence. Second one."
    assert ok is True
    final = rec.sent[-1]
    assert LIVE_MARKER_KEY not in final and LIVE_MARKER_KEY not in final["m.new_content"]
