"""
@file_name: test_claude_transcript.py
@date: 2026-07-29
@description: Pin the transcript builder — the file the CLI reads on `--resume`.

The whole point of authoring this file ourselves is that history stops riding in
the system prompt and starts riding in `messages`, where it sits AFTER the cache
prefix instead of inside it. Two properties make or break that:

**Determinism.** `messages` participates in the cache too (measured: a second
consecutive resume round's full-price input collapsed to 749/call). A rebuild
from the same turns must therefore be byte-identical — which rules out
wall-clock timestamps and random uuids. A drifting rebuild would still "work"
and would silently cost full price forever, so it is asserted here rather than
left to review.

**Structure.** Resume is not a plain log replay: the CLI walks the
`uuid`/`parentUuid` chain backwards from the `leafUuid` named in a trailing
`last-prompt` record. Records in the right order with a broken chain, or a
missing leaf pointer, yield a file that parses and resumes nothing.

Both were established empirically against CLI 2.1.220 (experiments E4/E5/E6 and
the T0 varied-session-id check) before this module existed; these tests keep the
production implementation honest to what was measured.
"""
from __future__ import annotations

import json

from xyz_agent_context.agent_framework.adapters.claude.transcript import (
    build_records,
    cwd_slug,
    render,
)

_ARGS = {
    "session_id": "11111111-2222-3333-4444-555555555555",
    "working_path": "/Volumes/Protagolabs/NarraNexus",
    "cli_version": "2.1.220",
    "git_branch": "main",
}

_TURNS = [
    {"role": "user", "content": "first question"},
    {"role": "assistant", "content": "first answer"},
    {"role": "user", "content": "second question"},
    {"role": "assistant", "content": "second answer"},
]


# --- determinism ------------------------------------------------------------


def test_same_turns_render_identical_bytes():
    a = render(build_records(_TURNS, **_ARGS))
    b = render(build_records(_TURNS, **_ARGS))
    assert a == b


def test_rendering_is_stable_across_a_delete_and_rebuild():
    """The production lifecycle is build → use → delete → rebuild. If the
    rebuild differed, every turn would pay full price."""
    first = render(build_records(_TURNS, **_ARGS))
    del_and_rebuild = render(build_records(list(_TURNS), **_ARGS))
    assert first == del_and_rebuild


def test_no_wall_clock_leaks_into_the_records():
    """Timestamps must be derived, not sampled — the failure mode of a sampled
    one is invisible in a single run."""
    records = build_records(_TURNS, **_ARGS)
    stamps = [r["timestamp"] for r in records if "timestamp" in r]
    assert stamps == sorted(stamps), "timestamps must be monotonic"
    assert all(s.startswith("2026-") or s.endswith("Z") for s in stamps)
    # Same input twice -> same stamps. A now()-based implementation fails here.
    again = [r["timestamp"] for r in build_records(_TURNS, **_ARGS) if "timestamp" in r]
    assert stamps == again


# --- structure the CLI actually walks --------------------------------------


def test_uuid_chain_is_linked_and_starts_at_null():
    records = [r for r in build_records(_TURNS, **_ARGS) if r["type"] != "last-prompt"]
    assert records[0]["parentUuid"] is None
    for prev, cur in zip(records, records[1:]):
        assert cur["parentUuid"] == prev["uuid"]


def test_uuids_are_unique():
    records = [r for r in build_records(_TURNS, **_ARGS) if r["type"] != "last-prompt"]
    uuids = [r["uuid"] for r in records]
    assert len(set(uuids)) == len(uuids)


def test_trailing_last_prompt_points_at_the_final_record():
    records = build_records(_TURNS, **_ARGS)
    assert records[-1]["type"] == "last-prompt"
    assert records[-1]["leafUuid"] == records[-2]["uuid"]
    assert records[-1]["sessionId"] == _ARGS["session_id"]


def test_roles_and_content_survive_in_order():
    """Content shape mirrors what the CLI writes itself: user rows carry a plain
    string, assistant rows a text block list. Normalizing both to one form would
    diverge from the format the CLI produces and reads."""
    records = build_records(_TURNS, **_ARGS)
    convo = [r for r in records if r["type"] in ("user", "assistant")]
    assert [r["type"] for r in convo] == [t["role"] for t in _TURNS]

    texts = []
    for r in convo:
        content = r["message"]["content"]
        if isinstance(content, str):
            texts.append(content)
        else:
            assert [b["type"] for b in content] == ["text"]
            texts.append(content[0]["text"])
    assert texts == [t["content"] for t in _TURNS]


def test_user_content_is_a_string_and_assistant_content_is_blocks():
    records = build_records(_TURNS, **_ARGS)
    for r in records:
        if r["type"] == "user":
            assert isinstance(r["message"]["content"], str)
        elif r["type"] == "assistant":
            assert isinstance(r["message"]["content"], list)


def test_every_record_carries_the_envelope_the_cli_expects():
    for r in build_records(_TURNS, **_ARGS):
        if r["type"] == "last-prompt":
            continue
        assert r["sessionId"] == _ARGS["session_id"]
        assert r["cwd"] == _ARGS["working_path"]
        assert r["version"] == _ARGS["cli_version"]
        assert r["gitBranch"] == _ARGS["git_branch"]
        assert r["isSidechain"] is False


def test_render_emits_one_json_object_per_line():
    text = render(build_records(_TURNS, **_ARGS))
    lines = text.splitlines()
    assert len(lines) == len(build_records(_TURNS, **_ARGS))
    for line in lines:
        json.loads(line)  # each line must stand alone
    assert text.endswith("\n")


# --- edges ------------------------------------------------------------------


def test_no_turns_yields_no_records():
    """An empty history has nothing to resume; the caller must fall back to a
    genuine first turn rather than write an empty file the CLI would reject."""
    assert build_records([], **_ARGS) == []
    assert render([]) == ""


def test_unknown_roles_are_dropped_not_passed_through():
    """`system` rows are already split out upstream; anything else reaching here
    is a bug elsewhere and must not become a malformed transcript record."""
    records = build_records(
        [{"role": "system", "content": "should not be here"},
         {"role": "user", "content": "kept"}],
        **_ARGS,
    )
    convo = [r for r in records if r["type"] in ("user", "assistant")]
    assert len(convo) == 1
    assert convo[0]["message"]["content"] == "kept"


def test_blank_content_turns_are_dropped():
    records = build_records(
        [{"role": "user", "content": ""},
         {"role": "assistant", "content": "   "},
         {"role": "user", "content": "real"}],
        **_ARGS,
    )
    convo = [r for r in records if r["type"] in ("user", "assistant")]
    assert [r["message"]["content"] for r in convo] == ["real"]


def test_different_session_ids_change_the_envelope_only():
    """T0 measured that envelope fields never reach the request; this keeps the
    builder consistent with that finding — same turns, same message payloads."""
    a = build_records(_TURNS, **{**_ARGS, "session_id": "aaaaaaaa-0000-0000-0000-000000000000"})
    b = build_records(_TURNS, **{**_ARGS, "session_id": "bbbbbbbb-0000-0000-0000-000000000000"})
    msgs_a = [r["message"] for r in a if r["type"] in ("user", "assistant")]
    msgs_b = [r["message"] for r in b if r["type"] in ("user", "assistant")]
    assert msgs_a == msgs_b
    # The envelope, by contrast, must differ — otherwise the ids were not
    # actually varied and the test proves nothing. `last-prompt` carries no
    # uuid of its own, hence the .get().
    assert [r.get("uuid") for r in a] != [r.get("uuid") for r in b]


# --- path convention --------------------------------------------------------


def test_cwd_slug_matches_the_observed_convention():
    assert cwd_slug("/Volumes/Protagolabs/NarraNexus") == "-Volumes-Protagolabs-NarraNexus"
    assert cwd_slug("/app") == "-app"


def test_cwd_slug_collapses_redundant_separators():
    assert cwd_slug("/app//sub/") == "-app-sub"
