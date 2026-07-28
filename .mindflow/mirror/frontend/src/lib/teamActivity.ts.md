---
code_file: frontend/src/lib/teamActivity.ts
last_verified: 2026-07-28
stub: false
---

# teamActivity.ts — the team-room status vocabulary

## Why it exists

Three surfaces render the same four states — the console summary, the console
row, the transcript bubble ([[TeamActivityConsole]]). Ordering, tone, duration
maths and i18n key mapping live here so they cannot disagree about what
`stalled` looks like, and so the logic is unit-testable without rendering.

## Design decisions

- **`stalled` is not a variant of `queued`.** `STATUS_RANK` puts it first: a
  queued turn has not started, a stalled turn started and went quiet. Showing
  both as "queued" is what let a wedged worker read as a busy room.
- `formatDuration` drops seconds past the hour — at that scale they are noise,
  and a multi-hour run is a first-class scenario (铁律 #14), not something to
  count down from. Negative deltas (clock skew) clamp to `0s` rather than
  rendering garbage.
- `buildTimeline` closes the last step at `now` for a live turn and at
  `endedAt` for a finished one, and flags the live one `ongoing` so the UI
  never implies a step ended when it hasn't.
- `hasRecentTurn` bounds how long a finished turn's trace stays on screen
  (`RECENT_TURN_WINDOW_MS`) — useful right after a reply, clutter an hour later.
- Ties break on name so rows don't jitter between 3s polls.
- Tones reference the semantic colour aliases, not palette entries, so dark
  mode follows.
