---
code_file: frontend/src/lib/teamActivity.ts
last_verified: 2026-07-30
stub: false
---

## 2026-07-30 — 裁掉 console 时代的三个符号

`hasRecentTurn` / `RECENT_TURN_WINDOW_MS` / `summarise` 删除：roster 常驻
显示全员后，「idle 痕迹保留窗口」和「折叠汇总条」都没有消费者了。文件头
说的"三处表面"如今是两处：roster 行与 roster 详情。


## 2026-07-30 — lastRunSummary（roster 空闲行）

`lastRunSummary(a, now)`：finished_at 缺失 → null（从未跑过）；否则
{durationMs, agoMs}。给右侧成员栏的空闲行画「ran 3m12s · 5m ago」——
比一句永远不变的"idle"多一层"它上次干了多久、多久之前"的可感知性。


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
