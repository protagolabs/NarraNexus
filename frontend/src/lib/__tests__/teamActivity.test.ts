/**
 * @file_name: teamActivity.test.ts
 * @description: The team-room activity vocabulary. Pins the four-state
 * ordering, the duration maths and the timeline construction, because three
 * surfaces (console summary, console row, transcript bubble) read from these
 * helpers and must never disagree about what a state looks like.
 */
import { describe, expect, test } from 'vitest';
import {
  lastRunSummary,
  RECENT_TURN_WINDOW_MS,
  STATUS_TONES,
  buildTimeline,
  compareActivity,
  elapsedSince,
  formatDuration,
  hasRecentTurn,
  phaseLabelKey,
  summarise,
  toMs,
} from '../teamActivity';
import type { TeamMemberActivity } from '@/types/teams';

const T0 = Date.parse('2026-07-28T09:00:00Z');

function member(
  agent_id: string,
  status: TeamMemberActivity['status'],
  extra: Partial<TeamMemberActivity> = {},
): TeamMemberActivity {
  return { agent_id, status, ...extra };
}

describe('formatDuration', () => {
  test('seconds below a minute', () => {
    expect(formatDuration(0)).toBe('0s');
    expect(formatDuration(12_400)).toBe('12s');
  });

  test('minutes keep zero-padded seconds so the width is stable', () => {
    expect(formatDuration(64_000)).toBe('1m04s');
    expect(formatDuration(59 * 60_000 + 59_000)).toBe('59m59s');
  });

  test('past an hour seconds are noise and get dropped', () => {
    expect(formatDuration(2 * 3_600_000 + 11 * 60_000 + 30_000)).toBe('2h11m');
  });

  test('a negative delta (clock skew) never renders as garbage', () => {
    expect(formatDuration(-5000)).toBe('0s');
  });
});

describe('toMs / elapsedSince', () => {
  test('absent timestamps are not an error state', () => {
    expect(toMs(null)).toBeNull();
    expect(toMs(undefined)).toBeNull();
    expect(toMs('not a date')).toBeNull();
    expect(elapsedSince(null, T0)).toBe('');
  });

  test('elapsed counts forward from the stamp', () => {
    expect(elapsedSince('2026-07-28T08:58:00Z', T0)).toBe('2m00s');
  });
});

describe('phaseLabelKey', () => {
  test('tool phases carry the tool name', () => {
    expect(phaseLabelKey('tool:WebSearch')).toEqual({
      key: 'chat.team.activity.tool',
      values: { name: 'WebSearch' },
    });
  });

  test('the known phases each get their own key', () => {
    expect(phaseLabelKey('starting').key).toBe('chat.team.activity.starting');
    expect(phaseLabelKey('thinking').key).toBe('chat.team.activity.thinking');
    expect(phaseLabelKey('replying').key).toBe('chat.team.activity.replying');
  });

  test('an unknown or missing phase falls back instead of leaking a raw token', () => {
    expect(phaseLabelKey('something-new').key).toBe('chat.team.activity.running');
    expect(phaseLabelKey(null).key).toBe('chat.team.activity.running');
  });
});

describe('buildTimeline', () => {
  const steps = [
    { phase: 'starting', at: '2026-07-28T08:59:00Z' },
    { phase: 'thinking', at: '2026-07-28T08:59:20Z' },
    { phase: 'tool:Read', at: '2026-07-28T08:59:50Z' },
  ];

  test('each step lasts until the next one', () => {
    const entries = buildTimeline(steps, T0, { live: true });
    expect(entries.map((e) => e.durationMs)).toEqual([20_000, 30_000, 10_000]);
  });

  test('only the last step of a live turn is ongoing', () => {
    const entries = buildTimeline(steps, T0, { live: true });
    expect(entries.map((e) => e.ongoing)).toEqual([false, false, true]);
  });

  test('a finished turn closes its last step at finished_at, not now', () => {
    const entries = buildTimeline(steps, T0, {
      live: false,
      endedAt: '2026-07-28T08:59:55Z',
    });
    expect(entries[2].durationMs).toBe(5_000);
    expect(entries[2].ongoing).toBe(false);
  });

  test('no steps means no timeline, not a crash', () => {
    expect(buildTimeline([], T0)).toEqual([]);
    expect(buildTimeline(undefined, T0)).toEqual([]);
    expect(buildTimeline(null, T0)).toEqual([]);
  });

  test('an unparseable stamp yields zero duration rather than NaN', () => {
    const entries = buildTimeline([{ phase: 'x', at: 'garbage' }], T0, { live: true });
    expect(entries[0].durationMs).toBe(0);
  });
});

describe('hasRecentTurn', () => {
  const withTrace = (finishedAt: string) =>
    member('a', 'idle', {
      finished_at: finishedAt,
      steps: { items: [{ phase: 'replying', at: finishedAt }], dropped: 0 },
    });

  test('a just-finished turn is still worth showing', () => {
    expect(hasRecentTurn(withTrace('2026-07-28T08:59:00Z'), T0)).toBe(true);
  });

  test('an old turn is not', () => {
    const old = new Date(T0 - RECENT_TURN_WINDOW_MS - 60_000).toISOString();
    expect(hasRecentTurn(withTrace(old), T0)).toBe(false);
  });

  test('idle with no trace shows nothing', () => {
    expect(hasRecentTurn(member('a', 'idle'), T0)).toBe(false);
  });

  test('a running member is never a "recent turn"', () => {
    expect(hasRecentTurn(member('a', 'running', { steps: { items: [], dropped: 0 } }), T0)).toBe(false);
  });
});

describe('summarise', () => {
  test('counts every state and flags the one that needs attention', () => {
    const s = summarise(
      [
        member('a', 'running'),
        member('b', 'running'),
        member('c', 'queued'),
        member('d', 'stalled'),
        member('e', 'idle'),
      ],
      T0,
    );
    expect(s).toMatchObject({ running: 2, queued: 1, stalled: 1, idle: 1, active: 4 });
    expect(s.needsAttention).toBe(true);
  });

  test('a quiet room needs no attention and shows no rows', () => {
    const s = summarise([member('a', 'idle'), member('b', 'idle')], T0);
    expect(s.active).toBe(0);
    expect(s.needsAttention).toBe(false);
  });

  test('an idle member with a fresh trace still counts as worth a row', () => {
    const s = summarise(
      [
        member('a', 'idle', {
          finished_at: '2026-07-28T08:59:00Z',
          steps: { items: [{ phase: 'replying', at: 'x' }], dropped: 0 },
        }),
      ],
      T0,
    );
    expect(s.active).toBe(1);
  });
});

describe('compareActivity', () => {
  const nameOf = (id: string) => id;

  test('attention first, idle last', () => {
    const sorted = [
      member('idle1', 'idle'),
      member('queued1', 'queued'),
      member('running1', 'running'),
      member('stalled1', 'stalled'),
    ].sort((a, b) => compareActivity(a, b, nameOf));
    expect(sorted.map((a) => a.status)).toEqual(['stalled', 'running', 'queued', 'idle']);
  });

  test('same state sorts by name so rows do not jitter between polls', () => {
    const sorted = [member('zoe', 'running'), member('ana', 'running')].sort((a, b) =>
      compareActivity(a, b, nameOf),
    );
    expect(sorted.map((a) => a.agent_id)).toEqual(['ana', 'zoe']);
  });
});

describe('STATUS_TONES', () => {
  test('every state has a label and an explanation', () => {
    for (const status of ['running', 'queued', 'stalled', 'idle'] as const) {
      expect(STATUS_TONES[status].labelKey).toMatch(/^chat\.team\.activity\./);
      expect(STATUS_TONES[status].hintKey).toMatch(/Hint$/);
    }
  });

  test('stalled and queued are visually distinct, not the same chip', () => {
    expect(STATUS_TONES.stalled.color).not.toBe(STATUS_TONES.queued.color);
    expect(STATUS_TONES.stalled.labelKey).not.toBe(STATUS_TONES.queued.labelKey);
  });
});

describe('lastRunSummary', () => {
  test('reports duration and how long ago for a finished turn', () => {
    const a = {
      agent_id: 'x', status: 'idle',
      started_at: '2026-07-30T10:00:00Z', finished_at: '2026-07-30T10:03:12Z',
    } as TeamMemberActivity;
    const now = Date.parse('2026-07-30T10:08:12Z');
    expect(lastRunSummary(a, now)).toEqual({ durationMs: 192_000, agoMs: 300_000 });
  });

  test('returns null when the member has never run', () => {
    const a = { agent_id: 'x', status: 'idle' } as TeamMemberActivity;
    expect(lastRunSummary(a, Date.now())).toBeNull();
  });
});
