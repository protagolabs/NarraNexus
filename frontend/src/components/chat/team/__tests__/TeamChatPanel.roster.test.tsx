/**
 * @file_name: TeamChatPanel.roster.test.tsx
 * @description: The two-pane team room's contract.
 *
 * Two decisions are pinned here, both learned from the folded console this
 * layout replaces:
 *   1. "who is in this room and what are they doing" is STANDING chrome — the
 *      roster lists every member without anyone expanding anything
 *   2. the transcript only ever carries a typing bubble for a member working
 *      RIGHT NOW; a finished turn leaves nothing behind, because its trace now
 *      lives one click away in the roster instead of piling up in the flow
 * plus the wiring between them: clicking a typing bubble opens that member's
 * process in the roster (one selection, two surfaces).
 */
import { describe, expect, test, vi, beforeEach } from 'vitest';
import { fireEvent, render, screen, within } from '@testing-library/react';

const getTeamChatMock = vi.fn();
const getEventLogMock = vi.fn();

vi.mock('@/lib/api', () => ({
  api: {
    getTeamChat: (...a: unknown[]) => getTeamChatMock(...a),
    getEventLog: (...a: unknown[]) => getEventLogMock(...a),
    getTranscriptionAvailability: () => Promise.resolve({ available: true, reason: '' }),
  },
}));

// The store hooks are selector-based; the fixtures are read at call time (the
// factory itself runs before the module body, so it must not touch them).
vi.mock('@/stores', () => ({
  useTeamsStore: (select: (s: unknown) => unknown) => select({ teams: TEAMS }),
  useConfigStore: (select: (s: unknown) => unknown) =>
    select({ agents: AGENTS, displayName: 'Bin', userId: 'usr_1' }),
}));

vi.mock('react-router-dom', async (importOriginal) => ({
  ...(await importOriginal<Record<string, unknown>>()),
  useNavigate: () => () => {},
}));

vi.mock('react-i18next', () => ({
  useTranslation: () => ({
    t: (k: string, v?: unknown) =>
      v && typeof v === 'object' ? `${k}(${Object.values(v).join(',')})` : k,
  }),
}));

import { TeamChatPanel } from '../TeamChatPanel';

const AGENTS = [
  { agent_id: 'a1', name: 'Ana' },
  { agent_id: 'a2', name: 'Bruno' },
];

const TEAMS = [
  {
    team: { team_id: 't1', name: 'Desk', owner_user_id: 'usr_1', source: 'local' },
    member_agent_ids: ['a1', 'a2'],
  },
];

const MESSAGE = {
  message_id: 'm1',
  from_agent: 'usr_usr_1',
  author_name: 'Bin',
  is_user: true,
  content: 'status?',
  created_at: '2026-07-30T09:00:00Z',
};

const RUNNING = {
  agent_id: 'a1',
  status: 'running' as const,
  phase: 'thinking',
  started_at: '2026-07-30T08:59:00Z',
};

const IDLE_WITH_TRACE = {
  agent_id: 'a2',
  status: 'idle' as const,
  started_at: '2026-07-30T08:50:00Z',
  finished_at: '2026-07-30T08:59:30Z',
  event_id: 'evt_1',
  steps: { items: [{ phase: 'thinking', at: '2026-07-30T08:50:10Z' }], dropped: 0 },
};

function typingButtons() {
  return screen
    .queryAllByRole('button')
    .filter((b) => (b.getAttribute('aria-label') || '').startsWith('chat.team.typing'));
}

async function renderRoom(activity: unknown[]) {
  getTeamChatMock.mockResolvedValue({
    success: true,
    messages: [MESSAGE],
    activity,
    lead_agent_id: 'a1',
  });
  const view = render(<TeamChatPanel teamId="t1" />);
  await screen.findByTestId('roster-row-a1');
  return view;
}

beforeEach(() => {
  getTeamChatMock.mockReset();
  getEventLogMock.mockReset();
  getEventLogMock.mockResolvedValue({ success: true, timeline: [] });
  // jsdom has no layout, so the transcript's "keep the tail in view" call would
  // throw before anything renders.
  Element.prototype.scrollIntoView = () => {};
});

describe('TeamChatPanel · two-pane room', () => {
  test('renders the roster panel with every member', async () => {
    await renderRoom([RUNNING, IDLE_WITH_TRACE]);

    // Both members have a standing row — the idle one is not hidden just
    // because it has nothing in flight.
    expect(within(screen.getByTestId('roster-row-a1')).getByText('Ana')).toBeTruthy();
    expect(within(screen.getByTestId('roster-row-a2')).getByText('Bruno')).toBeTruthy();
  });

  test('typing indicator appears only for running members', async () => {
    await renderRoom([RUNNING, IDLE_WITH_TRACE]);

    const typing = typingButtons();
    expect(typing).toHaveLength(1);
    expect(typing[0].getAttribute('aria-label')).toBe('chat.team.typing(Ana)');
  });

  test('clicking the typing indicator expands that member in the roster', async () => {
    await renderRoom([RUNNING, IDLE_WITH_TRACE]);

    fireEvent.click(typingButtons()[0]);

    // Every roster surface showing that member reflects the same selection.
    const rows = screen.getAllByTestId('roster-row-a1');
    expect(rows.length).toBeGreaterThan(0);
    for (const row of rows) expect(row.getAttribute('aria-expanded')).toBe('true');
    expect(
      screen.getAllByTestId('roster-row-a2').every((r) => r.getAttribute('aria-expanded') === 'false'),
    ).toBe(true);
  });

  test('no lingering bubbles for idle members with steps', async () => {
    await renderRoom([
      IDLE_WITH_TRACE,
      { ...IDLE_WITH_TRACE, agent_id: 'a1', event_id: 'evt_2' },
    ]);

    expect(typingButtons()).toHaveLength(0);
  });
});
