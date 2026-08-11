/**
 * @file_name: TeamWorkBoard.test.tsx
 * @description: The work board's contract with the user.
 *
 * The roster says who is busy right now; the board says what the team owes.
 * Pinned here are the two states that are the user's business specifically:
 *   1. `paused` is VISIBLE and resumable — a stopped task must not look
 *      deleted, and patrol deliberately will not un-park it (that would let a
 *      sweep undo the owner's stop)
 *   2. patrol's trace is shown here and nowhere else, because a healthy sweep
 *      says nothing in the room by design
 */
import { describe, expect, test, vi, beforeEach } from 'vitest';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { TeamWorkBoard } from '../TeamWorkBoard';

const getBoardMock = vi.fn();
const resumeMock = vi.fn();
const setPatrolMock = vi.fn();

vi.mock('@/lib/api', () => ({
  api: {
    getTeamWorkBoard: (...a: unknown[]) => getBoardMock(...a),
    resumeTeamWorkItem: (...a: unknown[]) => resumeMock(...a),
    setTeamPatrol: (...a: unknown[]) => setPatrolMock(...a),
  },
}));

vi.mock('react-i18next', () => ({
  useTranslation: () => ({
    t: (k: string, v?: unknown) =>
      v && typeof v === 'object' ? `${k}(${Object.values(v).join(',')})` : k,
  }),
}));

const NOW = Date.parse('2026-08-10T10:00:00Z');

function board(over: Record<string, unknown> = {}) {
  return {
    success: true,
    items: [],
    last_patrol_at: null,
    patrol_enabled: true,
    ...over,
  };
}

const LIVE = {
  item_id: 'wi_1', title: 'OCR the scans', assignee_id: 'a2',
  assignee_name: 'Bruno', status: 'in_progress',
};
const PARKED = {
  item_id: 'wi_2', title: 'draft summary', assignee_id: null,
  assignee_name: null, status: 'paused',
};

beforeEach(() => {
  getBoardMock.mockReset();
  resumeMock.mockReset();
  resumeMock.mockResolvedValue({ success: true });
  setPatrolMock.mockReset();
  setPatrolMock.mockResolvedValue({ success: true });
});

describe('TeamWorkBoard', () => {
  test('an empty board renders nothing at all', async () => {
    getBoardMock.mockResolvedValue(board());
    const { container } = render(<TeamWorkBoard teamId="t1" now={NOW} />);

    await waitFor(() => expect(getBoardMock).toHaveBeenCalled());
    // Not an empty-state header: permanent chrome for an absent thing is the
    // debt this room's design keeps paying off.
    expect(container.textContent).toBe('');
  });

  test('items show owner and status', async () => {
    getBoardMock.mockResolvedValue(board({ items: [LIVE] }));
    render(<TeamWorkBoard teamId="t1" now={NOW} />);

    expect(await screen.findByText('OCR the scans')).toBeTruthy();
    expect(screen.getByText(/Bruno/)).toBeTruthy();
  });

  test('unclaimed work says so', async () => {
    getBoardMock.mockResolvedValue(board({ items: [PARKED] }));
    render(<TeamWorkBoard teamId="t1" now={NOW} />);

    expect(await screen.findByText(/chat\.team\.board\.unclaimed/)).toBeTruthy();
  });

  test('a parked item is visible and offers resume', async () => {
    getBoardMock.mockResolvedValue(board({ items: [PARKED] }));
    render(<TeamWorkBoard teamId="t1" now={NOW} />);

    // Visible: a stopped task must not look deleted.
    expect(await screen.findByText('draft summary')).toBeTruthy();
    expect(screen.getByTestId('work-resume-wi_2')).toBeTruthy();
  });

  test('a live item offers no resume', async () => {
    getBoardMock.mockResolvedValue(board({ items: [LIVE] }));
    render(<TeamWorkBoard teamId="t1" now={NOW} />);

    await screen.findByText('OCR the scans');
    expect(screen.queryByTestId('work-resume-wi_1')).toBeNull();
  });

  test('resuming calls through and refreshes', async () => {
    getBoardMock.mockResolvedValue(board({ items: [PARKED] }));
    render(<TeamWorkBoard teamId="t1" now={NOW} />);

    fireEvent.click(await screen.findByTestId('work-resume-wi_2'));

    await waitFor(() => expect(resumeMock).toHaveBeenCalledWith('t1', 'wi_2'));
    // Refetched, so the row reflects its new state rather than a local guess.
    await waitFor(() => expect(getBoardMock.mock.calls.length).toBeGreaterThan(1));
  });

  test('patrol trace: never swept', async () => {
    getBoardMock.mockResolvedValue(board({ items: [LIVE], last_patrol_at: null }));
    render(<TeamWorkBoard teamId="t1" now={NOW} />);

    expect(await screen.findByText('chat.team.board.patrolPending')).toBeTruthy();
  });

  test('patrol trace: elapsed since the last sweep', async () => {
    getBoardMock.mockResolvedValue(board({
      items: [LIVE], last_patrol_at: '2026-08-10T09:48:00Z',
    }));
    render(<TeamWorkBoard teamId="t1" now={NOW} />);

    expect(await screen.findByText('chat.team.board.patrolledAgo(12m)')).toBeTruthy();
  });

  test('patrol trace: switched off', async () => {
    getBoardMock.mockResolvedValue(board({ items: [LIVE], patrol_enabled: false }));
    render(<TeamWorkBoard teamId="t1" now={NOW} />);

    expect(await screen.findByText('chat.team.board.patrolOff')).toBeTruthy();
  });
});

describe('TeamWorkBoard · patrol switch', () => {
  test('the sweep can be switched off from here', async () => {
    getBoardMock.mockResolvedValue(board({ items: [LIVE] }));
    render(<TeamWorkBoard teamId="t1" now={NOW} />);

    fireEvent.click(await screen.findByTestId('patrol-toggle'));

    await waitFor(() => expect(setPatrolMock).toHaveBeenCalledWith('t1', false));
  });

  test('a team with patrol OFF keeps the panel even with an empty board', async () => {
    // Otherwise the only control for a standing user setting disappears, and
    // there is no way to switch it back on.
    getBoardMock.mockResolvedValue(board({ items: [], patrol_enabled: false }));
    render(<TeamWorkBoard teamId="t1" now={NOW} />);

    expect(await screen.findByTestId('patrol-toggle')).toBeTruthy();
    expect(screen.getByText('chat.team.board.patrolOff')).toBeTruthy();
  });

  test('a failed toggle reverts', async () => {
    getBoardMock.mockResolvedValue(board({ items: [LIVE] }));
    setPatrolMock.mockRejectedValue(new Error('nope'));
    render(<TeamWorkBoard teamId="t1" now={NOW} />);

    fireEvent.click(await screen.findByTestId('patrol-toggle'));

    // Optimistic flip undone — the label must not claim a state the server
    // never accepted.
    await waitFor(() =>
      expect(screen.getByTestId('patrol-toggle').textContent).toBe('chat.team.board.turnOff'),
    );
  });
});
