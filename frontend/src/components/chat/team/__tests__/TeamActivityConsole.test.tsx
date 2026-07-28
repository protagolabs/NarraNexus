/**
 * @file_name: TeamActivityConsole.test.tsx
 * @description: The console's folding contract — the part of the design that
 * is easy to break silently.
 *
 * Three rules are pinned here, all of them UX decisions rather than
 * implementation details:
 *   1. a quiet room renders NOTHING (an empty panel is chrome, not information)
 *   2. the console stays folded by default — but a `stalled` member forces it
 *      open, because that is the one state the user should not have to find
 *   3. detail (the hint + step timeline) is one deliberate click away, never
 *      dumped on screen for six members at once
 */
import { describe, expect, test, vi } from 'vitest';
import { fireEvent, render, screen } from '@testing-library/react';
import { TeamActivityBubble, TeamActivityConsole } from '../TeamActivityConsole';
import type { TeamMemberActivity } from '@/types/teams';

vi.mock('react-i18next', () => ({
  useTranslation: () => ({
    t: (k: string, v?: Record<string, unknown>) =>
      v ? `${k}(${Object.values(v).join(',')})` : k,
  }),
}));

const NOW = Date.parse('2026-07-28T09:00:00Z');
const nameOf = (id: string) => ({ agent_a: 'Ana', agent_b: 'Bo', agent_c: 'Cy' })[id] ?? id;

function member(
  agent_id: string,
  status: TeamMemberActivity['status'],
  extra: Partial<TeamMemberActivity> = {},
): TeamMemberActivity {
  return { agent_id, status, ...extra };
}

const RUNNING = member('agent_a', 'running', {
  phase: 'tool:Read',
  tool_count: 4,
  started_at: '2026-07-28T08:58:00Z',
  last_signal_at: '2026-07-28T08:59:55Z',
  steps: {
    items: [
      { phase: 'starting', at: '2026-07-28T08:58:00Z' },
      { phase: 'tool:Read', at: '2026-07-28T08:58:30Z' },
    ],
    dropped: 0,
  },
});

describe('TeamActivityConsole', () => {
  test('a fully idle room renders nothing at all', () => {
    const { container } = render(
      <TeamActivityConsole
        activity={[member('agent_a', 'idle'), member('agent_b', 'idle')]}
        nameOf={nameOf}
        now={NOW}
      />,
    );
    expect(container).toBeEmptyDOMElement();
  });

  test('the summary line counts each state without expanding', () => {
    render(
      <TeamActivityConsole
        activity={[RUNNING, member('agent_b', 'queued'), member('agent_c', 'idle')]}
        nameOf={nameOf}
        now={NOW}
      />,
    );
    expect(screen.getByText(/countWorking\(1\)/)).toBeTruthy();
    expect(screen.getByText(/countWaiting\(1\)/)).toBeTruthy();
    // Folded: no member rows yet.
    expect(screen.queryByText('Ana')).toBeNull();
  });

  test('clicking the summary reveals one row per active member', () => {
    render(
      <TeamActivityConsole
        activity={[RUNNING, member('agent_b', 'queued'), member('agent_c', 'idle')]}
        nameOf={nameOf}
        now={NOW}
      />,
    );
    fireEvent.click(screen.getByText(/countWorking/));
    expect(screen.getByText('Ana')).toBeTruthy();
    expect(screen.getByText('Bo')).toBeTruthy();
    // Idle members are collapsed into a count, not given a row.
    expect(screen.queryByText('Cy')).toBeNull();
    expect(screen.getByText(/countIdle\(1\)/)).toBeTruthy();
  });

  test('a stalled member forces the console open without a click', () => {
    render(
      <TeamActivityConsole
        activity={[member('agent_a', 'stalled', { last_signal_at: '2026-07-28T08:50:00Z' })]}
        nameOf={nameOf}
        now={NOW}
      />,
    );
    expect(screen.getByText('Ana')).toBeTruthy();
    expect(screen.getByText(/silentFor/)).toBeTruthy();
  });

  test('stalled sorts above running so it is the first thing read', () => {
    render(
      <TeamActivityConsole
        activity={[RUNNING, member('agent_b', 'stalled')]}
        nameOf={nameOf}
        now={NOW}
      />,
    );
    const names = screen.getAllByText(/^(Ana|Bo)$/).map((el) => el.textContent);
    expect(names).toEqual(['Bo', 'Ana']);
  });

  test('the step timeline is one click deeper, not shown up front', () => {
    render(<TeamActivityConsole activity={[RUNNING]} nameOf={nameOf} now={NOW} />);
    fireEvent.click(screen.getByText(/countWorking/));
    expect(screen.queryByText(/runningHint/)).toBeNull();

    fireEvent.click(screen.getByText('Ana'));
    expect(screen.getByText(/runningHint/)).toBeTruthy();
    // Both recorded phases appear, and the live one is marked ongoing.
    expect(screen.getByText('chat.team.activity.starting')).toBeTruthy();
    expect(screen.getByText(/stepOngoing/)).toBeTruthy();
  });

  test('queued shows how long it has waited, not a bare word', () => {
    render(
      <TeamActivityConsole
        activity={[member('agent_a', 'queued', {
          queued_count: 2,
          queued_since: '2026-07-28T08:55:00Z',
        })]}
        nameOf={nameOf}
        now={NOW}
      />,
    );
    fireEvent.click(screen.getByText(/countWaiting/));
    expect(screen.getByText(/waitingFor\(5m00s\)/)).toBeTruthy();
    expect(screen.getByText('×2')).toBeTruthy();
  });
});

describe('TeamActivityBubble', () => {
  test('carries phase, tool count and elapsed without any interaction', () => {
    render(<TeamActivityBubble activity={RUNNING} name="Ana" now={NOW} />);
    expect(screen.getByText('chat.team.activity.tool(Read)')).toBeTruthy();
    expect(screen.getByText('4')).toBeTruthy();
    expect(screen.getByText('2m00s')).toBeTruthy();
  });

  test('expands to the same timeline in place', () => {
    render(<TeamActivityBubble activity={RUNNING} name="Ana" now={NOW} />);
    expect(screen.queryByText(/runningHint/)).toBeNull();
    fireEvent.click(screen.getByText('chat.team.activity.tool(Read)'));
    expect(screen.getByText(/runningHint/)).toBeTruthy();
  });

  test('a member with no steps has nothing to expand', () => {
    render(
      <TeamActivityBubble
        activity={member('agent_a', 'queued', { queued_since: '2026-07-28T08:59:00Z' })}
        name="Ana"
        now={NOW}
      />,
    );
    fireEvent.click(screen.getByText('chat.team.activity.queued'));
    expect(screen.queryByText(/queuedHint/)).toBeNull();
  });
});
