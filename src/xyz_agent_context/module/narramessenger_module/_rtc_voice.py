"""
@file_name: _rtc_voice.py
@date: 2026-08-06
@description: F28 RTC voice-input metadata — v1 parser + body envelope splitter.

Contract source: Hybrid "Direct Matrix RTC fast reply" handoff, sections 3.1/3.2/4.2.
The metadata is a performance / presentation hint, NOT an authorization
credential: callers must keep every existing sender / room check in place.
Any validation miss degrades the event to a normal Matrix text message —
it must never break the normal reply path.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Optional, Tuple

RTC_VOICE_INPUT_KEY = "ai.netmind.rtc.voice_input"

_BINDING_ID_FIELDS = (
    "rtc_session_id",
    "turn_id",
    "invocation_id",
    "agent_profile_id",
)

# Envelope must sit at the very start of the body (leading whitespace aside);
# anywhere else it is user-authored text, not a delivery envelope.
_ENVELOPE_RE = re.compile(
    r"\A\s*<narra-system-prompt\b[^>]*>(.*?)</narra-system-prompt>\s*",
    re.DOTALL,
)


@dataclass(frozen=True)
class RtcVoiceInputV1:
    rtc_session_id: str
    turn_id: str
    invocation_id: str
    agent_profile_id: str
    voice_instructions: Optional[str] = None


def _is_strict_int(value: Any, expected: int) -> bool:
    # bool is an int subclass in Python; the contract's `=== 1` excludes it.
    return type(value) is int and value == expected


def _non_blank_str(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def parse_rtc_voice_input(event: dict) -> Optional[RtcVoiceInputV1]:
    """Return the validated v1 payload, or None to mean "normal text message".

    Implements every §3.2 rule strictly; voice_instructions is the only
    forgiving field (missing / blank / mistyped -> None, turn stays valid).
    """
    if not isinstance(event, dict) or event.get("type") != "m.room.message":
        return None
    content = event.get("content")
    if not isinstance(content, dict) or content.get("msgtype") != "m.text":
        return None
    meta = content.get(RTC_VOICE_INPUT_KEY)
    if not isinstance(meta, dict):
        return None
    if not _is_strict_int(meta.get("version"), 1):
        return None
    if meta.get("transport") != "matrix":
        return None
    if not _is_strict_int(meta.get("seq"), 1):
        return None
    if meta.get("transcript_final") is not True:
        return None
    if not all(_non_blank_str(meta.get(field)) for field in _BINDING_ID_FIELDS):
        return None

    instructions = meta.get("voice_instructions")
    if not _non_blank_str(instructions):
        instructions = None

    return RtcVoiceInputV1(
        rtc_session_id=meta["rtc_session_id"],
        turn_id=meta["turn_id"],
        invocation_id=meta["invocation_id"],
        agent_profile_id=meta["agent_profile_id"],
        voice_instructions=instructions,
    )


def split_narra_system_prompt(body: str) -> Tuple[Optional[str], str]:
    """Split the <narra-system-prompt> envelope off the transcript.

    Returns (envelope_text, transcript). A malformed, unclosed, or
    mid-body envelope is NOT stripped: the whole body stays transcript,
    so raw user text can never be promoted to instruction content.
    """
    match = _ENVELOPE_RE.match(body)
    if match is None:
        return None, body
    return match.group(1).strip(), body[match.end():]
