---
code_file: frontend/src/components/chat/team/TeamActivityConsole.tsx
last_verified: 2026-07-28
stub: false
---

# team/TeamActivityConsole.tsx — layered live status for a multi-agent room

## Why it exists

The single-agent treatment does not transfer. [[TurnTimeline]] +
[[ExecutionPopover]] can fill the pane because a 1:1 chat has exactly one
runner; a team room can have six at once, and six timelines bury the
transcript they are supposed to annotate. The previous team UI went the other
way and showed a single word per member ("queued") with no elapsed, no tool
count and no trace — during a 25-minute prod run the user saw nothing but that
word and assumed the product had hung.

So the information is **layered**, densest last:

| | what | where |
|---|---|---|
| L0 | one summary line (`2 working · 1 waiting`) | always visible |
| L1 | one row per non-idle member: status, phase, tools, elapsed | on expand |
| L2 | that member's step timeline with per-phase durations | on row expand |

## Design decisions

- **Folded by default, except for `stalled`.** `summarise().needsAttention`
  force-expands the console when a member's heartbeat has died. Everything else
  respects the user's fold — an always-open panel is chrome.
- **Renders `null` for a quiet room.** An empty status panel is not information.
- **Two surfaces, no cross-wiring.** The console is the overview; the
  `TeamActivityBubble` sits at the foot of the transcript where the eye already
  is while waiting, carries L0+L1 for one member, and expands to the same L2 in
  place via its own state. Neither drives the other.
- **Ongoing steps say so.** `buildTimeline` flags the final step of a live turn
  so it renders "12s, ongoing" instead of a duration that implies it finished.
- **Idle members with a fresh trace still get a row** (`hasRecentTurn`), so
  "what did it just do" survives the turn ending.
- Vocabulary — ordering, tones, duration maths, i18n key mapping — lives in
  [[teamActivity]], not here, so the three surfaces cannot drift.

## Gotchas

- `now` is passed in, never read from the clock inside a row: every duration on
  screen must come from one instant or rows disagree by a tick.
- Status colours use the semantic aliases (`--color-warning` / `--color-error`),
  not raw palette entries — those are what the dark theme re-points.
