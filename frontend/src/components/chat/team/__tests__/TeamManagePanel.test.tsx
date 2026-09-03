/**
 * @file_name: TeamManagePanel.test.tsx
 * @author: NarraNexus
 * @date: 2026-09-03
 * @description: The management tab does what the four surfaces it replaced
 * did — bulletin, lead, patrol, members, clear, delete — through the same
 * calls, from one place.
 */
import { describe, expect, test, vi, beforeEach } from 'vitest';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';

const getBoardMock = vi.fn();
const setPatrolMock = vi.fn();
const clearDataMock = vi.fn();
const addMemberMock = vi.fn();
const removeMemberMock = vi.fn();
const deleteTeamMock = vi.fn();
const navigateMock = vi.fn();
const confirmMock = vi.fn();

vi.mock('@/lib/api', () => ({
  api: {
    getTeamWorkBoard: (...a: unknown[]) => getBoardMock(...a),
    setTeamPatrol: (...a: unknown[]) => setPatrolMock(...a),
    clearTeamData: (...a: unknown[]) => clearDataMock(...a),
  },
}));

vi.mock('@/stores', () => ({
  useTeamsStore: (select: (s: unknown) => unknown) =>
    select({
      teams: [],
      refresh: vi.fn(),
      addMember: (...a: unknown[]) => addMemberMock(...a),
      removeMember: (...a: unknown[]) => removeMemberMock(...a),
      deleteTeam: (...a: unknown[]) => deleteTeamMock(...a),
    }),
  useConfigStore: (select: (s: unknown) => unknown) => select({ agents: [] }),
}));

vi.mock('@/components/ui', () => ({
  Button: ({ children, onClick, ...rest }: React.ComponentProps<'button'>) => (
    <button onClick={onClick} data-testid={rest['data-testid' as keyof typeof rest] as string}>
      {children}
    </button>
  ),
  useNotice: () => ({
    confirm: (...a: unknown[]) => confirmMock(...a),
    notifyError: vi.fn(),
    dialog: null,
  }),
}));

// The modal and the clear dialog are exercised by their own tests; here they
// only need to be mountable.
vi.mock('@/components/teams/TeamManagementModal', () => ({
  TeamManagementModal: ({ open }: { open: boolean }) =>
    open ? <div data-testid="profile-modal" /> : null,
}));
vi.mock('@/components/teams/ClearTeamDataDialog', () => ({
  ClearTeamDataDialog: ({
    onConfirm,
  }: {
    onConfirm: (s: { chat: boolean; files: boolean; bulletin: boolean }) => void;
  }) => (
    <button
      data-testid="clear-confirm"
      onClick={() => onConfirm({ chat: true, files: false, bulletin: true })}
    >
      confirm
    </button>
  ),
}));
vi.mock('../TeamBulletinPanel', () => ({
  TeamBulletinPanel: () => <div data-testid="bulletin-panel" />,
}));

vi.mock('react-router-dom', () => ({ useNavigate: () => navigateMock }));
vi.mock('react-i18next', () => ({
  useTranslation: () => ({
    t: (k: string, v?: unknown) =>
      v && typeof v === 'object' ? `${k}(${Object.values(v).join(',')})` : k,
  }),
}));

import { TeamManagePanel } from '../TeamManagePanel';

const ANA = { agent_id: 'a1', name: 'Ana' };
const BRUNO = { agent_id: 'a2', name: 'Bruno' };
const CY = { agent_id: 'a3', name: 'Cy' };

function renderPanel(over: Partial<React.ComponentProps<typeof TeamManagePanel>> = {}) {
  const props: React.ComponentProps<typeof TeamManagePanel> = {
    teamId: 't1',
    teamName: 'Desk',
    members: [ANA, BRUNO] as never,
    allAgents: [ANA, BRUNO, CY] as never,
    leadAgentId: 'a1',
    onSetLead: vi.fn(),
    bulletin: null,
    bulletinLoading: false,
    bulletinError: null,
    memberNames: { a1: 'Ana', a2: 'Bruno' },
    onBulletinAdd: vi.fn(),
    onBulletinEdit: vi.fn(),
    onBulletinDelete: vi.fn(),
    onBulletinClearTier: vi.fn(),
    onCleared: vi.fn(),
    ...over,
  };
  render(<TeamManagePanel {...props} />);
  return props;
}

beforeEach(() => {
  vi.clearAllMocks();
  getBoardMock.mockResolvedValue({ success: true, items: [], last_patrol_at: null, patrol_enabled: true });
  setPatrolMock.mockResolvedValue({ success: true });
  clearDataMock.mockResolvedValue({ success: true });
  addMemberMock.mockResolvedValue(undefined);
  removeMemberMock.mockResolvedValue(undefined);
  deleteTeamMock.mockResolvedValue(undefined);
  confirmMock.mockResolvedValue(true);
});

describe('TeamManagePanel', () => {
  test('the bulletin is the first thing in the tab', () => {
    renderPanel();
    expect(screen.getByTestId('bulletin-panel')).toBeTruthy();
  });

  test('choosing a lead calls back with the member id', () => {
    const props = renderPanel();
    fireEvent.change(screen.getByTestId('manage-lead-select'), { target: { value: 'a2' } });
    expect(props.onSetLead).toHaveBeenCalledWith('a2');
  });

  test('patrol is read from the board and written through the PUT', async () => {
    renderPanel();
    const toggle = await screen.findByTestId('patrol-toggle');
    await waitFor(() => expect(toggle.textContent).toBe('chat.team.board.turnOff'));

    fireEvent.click(toggle);

    await waitFor(() => expect(setPatrolMock).toHaveBeenCalledWith('t1', false));
    expect(toggle.textContent).toBe('chat.team.board.turnOn');
  });

  test('a failed patrol write reverts the switch', async () => {
    setPatrolMock.mockRejectedValue(new Error('nope'));
    renderPanel();
    const toggle = await screen.findByTestId('patrol-toggle');
    await waitFor(() => expect(toggle.textContent).toBe('chat.team.board.turnOff'));

    fireEvent.click(toggle);

    await waitFor(() => expect(toggle.textContent).toBe('chat.team.board.turnOff'));
  });

  test('members are added and removed through the store', async () => {
    renderPanel();
    fireEvent.click(screen.getByTestId('manage-member-a3'));
    await waitFor(() => expect(addMemberMock).toHaveBeenCalledWith('t1', 'a3'));

    fireEvent.click(screen.getByTestId('manage-member-a2'));
    await waitFor(() => expect(removeMemberMock).toHaveBeenCalledWith('t1', 'a2'));
  });

  test('edit profile opens the existing modal', () => {
    renderPanel();
    fireEvent.click(screen.getByTestId('manage-edit-profile'));
    expect(screen.getByTestId('profile-modal')).toBeTruthy();
  });

  test('clearing data reports the scopes back so the room can drop them', async () => {
    const props = renderPanel();
    fireEvent.click(screen.getByTestId('manage-clear-data'));
    fireEvent.click(screen.getByTestId('clear-confirm'));

    await waitFor(() =>
      expect(clearDataMock).toHaveBeenCalledWith('t1', { chat: true, files: false, bulletin: true }),
    );
    await waitFor(() =>
      expect(props.onCleared).toHaveBeenCalledWith({ chat: true, files: false, bulletin: true }),
    );
  });

  test('deleting asks first, then leaves the room', async () => {
    renderPanel();
    fireEvent.click(screen.getByTestId('manage-delete-team'));

    await waitFor(() => expect(deleteTeamMock).toHaveBeenCalledWith('t1'));
    expect(confirmMock).toHaveBeenCalled();
    expect(navigateMock).toHaveBeenCalledWith('/app/chat');
  });

  test('a declined delete confirmation deletes nothing', async () => {
    confirmMock.mockResolvedValue(false);
    renderPanel();
    fireEvent.click(screen.getByTestId('manage-delete-team'));

    await waitFor(() => expect(confirmMock).toHaveBeenCalled());
    expect(deleteTeamMock).not.toHaveBeenCalled();
    expect(navigateMock).not.toHaveBeenCalled();
  });
});
