"""
@file_name: test_grep_redos.py
@author:
@date: 2026-08-10
@description: grep_filter's regex path must be ReDoS-safe — the pattern is
agent/LLM-supplied and, once grep is served over HTTP, an unbounded match would
wedge the shared API loop. These pin: (1) an adversarial backtracking pattern
returns within the budget instead of hanging; (2) normal regex + substring + the
invalid-regex fallback all still work.
"""
from __future__ import annotations

import time

from xyz_agent_context.memory._memory_impl.retrieval import (
    grep_filter,
    _GREP_PER_MATCH_TIMEOUT_S,
    _GREP_TOTAL_BUDGET_S,
)


class _Rec:
    """Minimal stand-in — grep_filter only reads .content_text."""
    def __init__(self, text):
        self.content_text = text


def test_redos_pattern_is_bounded_not_hung():
    # (a|aa)+$ on a long non-matching string is catastrophic backtracking in a
    # naive engine. With the per-match timeout it must return quickly (skipping
    # the record), NOT run to completion. Non-vacuous: stdlib re here would hang.
    recs = [_Rec("a" * 60 + "!")]
    t = time.time()
    out = grep_filter(recs, r"(a|aa)+$", regex=True)
    elapsed = time.time() - t
    assert out == []  # timed-out record is treated as a non-match
    # generous ceiling: one record * per-match timeout, well under the total budget
    assert elapsed < _GREP_PER_MATCH_TIMEOUT_S + _GREP_TOTAL_BUDGET_S + 1.0


def test_normal_regex_still_matches():
    recs = [_Rec("order id agent_abc123 shipped"), _Rec("nothing here")]
    out = grep_filter(recs, r"agent_\w+", regex=True)
    assert [r.content_text for r in out] == ["order id agent_abc123 shipped"]


def test_regex_is_case_insensitive_by_default():
    recs = [_Rec("HELLO World")]
    assert len(grep_filter(recs, r"hello", regex=True)) == 1


def test_invalid_regex_falls_back_to_substring():
    # "(" is an invalid pattern; grep_filter must not raise — it treats the
    # pattern as a literal substring instead.
    recs = [_Rec("a literal ( paren"), _Rec("no match")]
    out = grep_filter(recs, "(", regex=True)
    assert [r.content_text for r in out] == ["a literal ( paren"]


def test_substring_mode_unaffected():
    recs = [_Rec("find the ORDER-123 token"), _Rec("unrelated")]
    assert len(grep_filter(recs, "order-123", regex=False)) == 1  # case-insensitive substring
