/**
 * @file_name: messengerUtils.test.ts
 * @description: Behavior coverage for the Messenger row's pure helpers —
 * last-message preview derivation and most-recent-first agent/team ordering.
 */

import { describe, expect, it } from 'vitest';
import {
  computeRowMeta,
  computeTeamRowMeta,
  sortAgentsByActivity,
  sortMessengerItems,
} from '../messengerUtils';

describe('computeRowMeta', () => {
  it('prefers a fresher local session message over a stale server preview', () => {
    const agent = {
      agent_id: 'agt_1',
      last_assistant_preview: 'stale server reply',
      last_assistant_at: '2026-08-01T00:00:00.000Z',
    };
    const sessionMessages = [
      { role: 'user', content: 'hi', timestamp: 1_800_000_000_000 },
      { role: 'assistant', content: 'fresh local reply', timestamp: 1_800_000_001_000 },
    ];

    const meta = computeRowMeta(agent, sessionMessages);

    expect(meta.preview).toBe('fresh local reply');
    expect(meta.timeMs).toBe(1_800_000_001_000);
  });

  it('falls back to the server preview when there is no local session', () => {
    const agent = {
      agent_id: 'agt_2',
      last_assistant_preview: 'server reply',
      last_assistant_at: '2026-08-01T00:00:00.000Z',
    };

    const meta = computeRowMeta(agent, []);

    expect(meta.preview).toBe('server reply');
    expect(meta.timeMs).toBe(new Date('2026-08-01T00:00:00.000Z').getTime());
  });

  it('collapses whitespace and truncates preview text to 60 chars', () => {
    const agent = { agent_id: 'agt_3' };
    const longText = 'a'.repeat(80);
    const sessionMessages = [
      { role: 'assistant', content: `line one\n\n  line   two ${longText}`, timestamp: 100 },
    ];

    const meta = computeRowMeta(agent, sessionMessages);

    expect(meta.preview).toHaveLength(60);
    expect(meta.preview).not.toMatch(/\s{2,}/);
  });

  it('returns an empty preview and zero time when nothing is available', () => {
    const meta = computeRowMeta({ agent_id: 'agt_4' }, []);

    expect(meta.preview).toBe('');
    expect(meta.timeMs).toBe(0);
  });
});

describe('sortAgentsByActivity', () => {
  it('floats the most recently active agent to the top', () => {
    const agents = [
      { agent_id: 'old', last_assistant_at: '2026-08-01T00:00:00.000Z' },
      { agent_id: 'new', last_assistant_at: '2026-08-20T00:00:00.000Z' },
    ];

    const sorted = sortAgentsByActivity(agents, () => 0);

    expect(sorted.map((a) => a.agent_id)).toEqual(['new', 'old']);
  });

  it('lets a fresh local message outrank a stale server timestamp', () => {
    const agents = [
      { agent_id: 'server-fresh', last_assistant_at: '2026-08-20T00:00:00.000Z' },
      { agent_id: 'local-fresh', last_assistant_at: '2026-08-01T00:00:00.000Z' },
    ];
    const localActivityMs = (agentId: string) =>
      agentId === 'local-fresh' ? new Date('2026-08-25T00:00:00.000Z').getTime() : 0;

    const sorted = sortAgentsByActivity(agents, localActivityMs);

    expect(sorted.map((a) => a.agent_id)).toEqual(['local-fresh', 'server-fresh']);
  });

  it('falls back to created_at for an agent with no conversation yet', () => {
    const agents = [
      { agent_id: 'chatted', last_assistant_at: '2026-08-01T00:00:00.000Z' },
      { agent_id: 'never-chatted', created_at: '2026-08-20T00:00:00.000Z' },
    ];

    const sorted = sortAgentsByActivity(agents, () => 0);

    expect(sorted.map((a) => a.agent_id)).toEqual(['never-chatted', 'chatted']);
  });

  it('breaks exact ties by agent_id so order does not churn between renders', () => {
    const agents = [
      { agent_id: 'b', last_assistant_at: '2026-08-01T00:00:00.000Z' },
      { agent_id: 'a', last_assistant_at: '2026-08-01T00:00:00.000Z' },
    ];

    const sorted = sortAgentsByActivity(agents, () => 0);

    expect(sorted.map((a) => a.agent_id)).toEqual(['a', 'b']);
  });
});

describe('computeTeamRowMeta', () => {
  it('derives preview + time from the server last-message fields', () => {
    const meta = computeTeamRowMeta({
      last_message_preview: 'line one\n\n  line   two',
      last_message_at: '2026-08-01T00:00:00.000Z',
    });

    expect(meta.preview).toBe('line one line two');
    expect(meta.timeMs).toBe(new Date('2026-08-01T00:00:00.000Z').getTime());
  });

  it('returns an empty preview and zero time when the room has said nothing yet', () => {
    const meta = computeTeamRowMeta({});

    expect(meta.preview).toBe('');
    expect(meta.timeMs).toBe(0);
  });
});

describe('sortMessengerItems', () => {
  it('interleaves agents and teams on one most-recent-first clock', () => {
    const agents = [
      { agent_id: 'agt_old', last_assistant_at: '2026-08-01T00:00:00.000Z' },
      { agent_id: 'agt_new', last_assistant_at: '2026-08-24T00:00:00.000Z' },
    ];
    const teams = [
      { team_id: 'team_mid', last_message_at: '2026-08-10T00:00:00.000Z' },
    ];

    const items = sortMessengerItems(agents, teams, () => 0);

    expect(items).toEqual([
      { kind: 'agent', id: 'agt_new' },
      { kind: 'team', id: 'team_mid' },
      { kind: 'agent', id: 'agt_old' },
    ]);
  });

  it('falls back to a team\'s created_at for a room with no messages yet', () => {
    const teams = [
      { team_id: 'chatted', last_message_at: '2026-08-01T00:00:00.000Z' },
      { team_id: 'never-used', created_at: '2026-08-20T00:00:00.000Z' },
    ];

    const items = sortMessengerItems([], teams, () => 0);

    expect(items.map((i) => i.id)).toEqual(['never-used', 'chatted']);
  });

  it('breaks exact cross-kind ties by id', () => {
    const agents = [{ agent_id: 'b', last_assistant_at: '2026-08-01T00:00:00.000Z' }];
    const teams = [{ team_id: 'a', last_message_at: '2026-08-01T00:00:00.000Z' }];

    const items = sortMessengerItems(agents, teams, () => 0);

    expect(items).toEqual([
      { kind: 'team', id: 'a' },
      { kind: 'agent', id: 'b' },
    ]);
  });
});
