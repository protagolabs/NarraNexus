/**
 * The management modal has exactly ONE save, and it writes profile + lead
 * from live state in a single call. (2026-09-03: two same-named saves that
 * each wrote half reset the other half's draft on the refresh that followed.)
 */

import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';

const updateTeam = vi.fn().mockResolvedValue(undefined);

const teams = [
  {
    team: { team_id: 't1', name: 'Desk', color: '#3b82f6', intro_md: 'hi', lead_agent_id: null, source: 'local' },
    member_agent_ids: ['a1', 'a2'],
  },
];

vi.mock('@/stores', () => ({
  useTeamsStore: () => ({
    teams,
    refresh: vi.fn(),
    createTeam: vi.fn(),
    updateTeam,
    deleteTeam: vi.fn(),
    addMember: vi.fn(),
    removeMember: vi.fn(),
    loading: false,
  }),
  useConfigStore: () => ({ agents: [{ agent_id: 'a1', name: 'Ana' }, { agent_id: 'a2', name: 'Bruno' }] }),
}));

vi.mock('@/components/ui', async (importOriginal) => {
  const mod = await importOriginal<typeof import('@/components/ui')>();
  return {
    ...mod,
    useNotice: () => ({ confirm: vi.fn().mockResolvedValue(true), notifyError: vi.fn(), dialog: null }),
  };
});

import { TeamManagementModal } from '../TeamManagementModal';

describe('TeamManagementModal · one save', () => {
  beforeEach(() => updateTeam.mockClear());

  it('renders a single save button', async () => {
    render(<TeamManagementModal open initialTeamId="t1" onClose={vi.fn()} />);
    await waitFor(() => expect(screen.getByDisplayValue('Desk')).toBeInTheDocument());
    expect(screen.getAllByRole('button', { name: /save changes/i })).toHaveLength(1);
  });

  it('saves a changed name AND a changed lead in one call', async () => {
    render(<TeamManagementModal open initialTeamId="t1" onClose={vi.fn()} />);
    await waitFor(() => expect(screen.getByDisplayValue('Desk')).toBeInTheDocument());

    fireEvent.change(screen.getByDisplayValue('Desk'), { target: { value: 'Desk 2' } });
    fireEvent.change(screen.getByRole('combobox'), { target: { value: 'a2' } });
    fireEvent.click(screen.getByRole('button', { name: /save changes/i }));

    await waitFor(() =>
      expect(updateTeam).toHaveBeenCalledWith('t1', {
        name: 'Desk 2',
        color: '#3b82f6',
        intro_md: 'hi',
        lead_agent_id: 'a2',
      }),
    );
  });
});
