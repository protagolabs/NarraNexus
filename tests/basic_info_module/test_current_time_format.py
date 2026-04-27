"""
@file_name: test_current_time_format.py
@date: 2026-04-21
@description: Lock the contract of BasicInfoModule's current_time injection.

Symptom this guards against: an agent, asked for "today's meetings",
returned three meetings — but two were actually yesterday. The agent saw
times like 22:07 while it was currently 17:45 and rationalized the
mismatch as "server relative time" instead of catching it. Root cause on
the prompt side: current_time was `datetime.now().isoformat()` → naive,
no timezone, no weekday, potentially in server's local clock instead of
the user's. The agent had no reliable anchor to sanity-check against.

New contract:
  - Resolved in the user's timezone (IANA string)
  - Explicit UTC offset included (e.g. "+08:00")
  - Weekday label for extra human anchor
  - Invalid/missing tz falls back to UTC with a clean "UTC" label
    (never echoes the invalid tz string back as a label)
"""

import re

from xyz_agent_context.module.basic_info_module.basic_info_module import (
    _format_current_time_for_agent,
)


_BASE_PATTERN = re.compile(
    r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2} [+-]\d{2}:\d{2} "
    r"\((Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday), [^)]+\)$"
)


def test_format_has_offset_weekday_and_tz_label():
    s = _format_current_time_for_agent("Asia/Shanghai")
    assert _BASE_PATTERN.match(s), f"unexpected shape: {s!r}"
    assert "+08:00" in s
    assert "Asia/Shanghai" in s


def test_utc_timezone_shows_plus_00_offset():
    s = _format_current_time_for_agent("UTC")
    assert _BASE_PATTERN.match(s)
    assert "+00:00" in s
    assert "UTC" in s


def test_empty_timezone_falls_back_to_utc():
    s = _format_current_time_for_agent("")
    assert _BASE_PATTERN.match(s)
    assert "+00:00" in s
    # fallback must label as UTC, not echo the empty string
    assert s.rstrip(")").endswith("UTC")


def test_invalid_timezone_falls_back_to_utc_cleanly():
    """Guard against echoing the invalid tz in the label — observed before
    the `is_valid_timezone` check was added."""
    s = _format_current_time_for_agent("Not/A/Zone")
    assert _BASE_PATTERN.match(s)
    assert "Not/A/Zone" not in s
    assert "UTC" in s
    assert "+00:00" in s


def test_weekday_matches_date():
    """Weekday label must be consistent with the calendar date emitted."""
    from datetime import date
    s = _format_current_time_for_agent("UTC")
    # Extract "YYYY-MM-DD" + weekday
    m = re.match(r"^(\d{4}-\d{2}-\d{2})\s.*\((\w+),", s)
    assert m, f"unexpected format: {s!r}"
    date_str, weekday_str = m.group(1), m.group(2)
    expected = ["Monday", "Tuesday", "Wednesday", "Thursday",
                "Friday", "Saturday", "Sunday"][date.fromisoformat(date_str).weekday()]
    assert weekday_str == expected


def test_format_is_stable_within_one_second():
    """Two successive calls should agree on the minute (rare race at the
    second boundary is tolerated)."""
    a = _format_current_time_for_agent("UTC")
    b = _format_current_time_for_agent("UTC")
    # Compare the first 16 chars = "YYYY-MM-DD HH:MM"
    assert a[:16] == b[:16]
