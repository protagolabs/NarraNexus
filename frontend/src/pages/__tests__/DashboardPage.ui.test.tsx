/**
 * @file_name: DashboardPage.ui.test.tsx
 * @author: NexusAgent
 * @date: 2026-08-24
 * @description: Regression coverage for the streamlined Agents dashboard shell.
 */
import { fireEvent, render, screen } from '@testing-library/react';
import { MemoryRouter, useLocation } from 'react-router-dom';
import { describe, expect, test, vi } from 'vitest';

const { dashboardState, configState, teamsState, chatState } = vi.hoisted(() => ({
  dashboardState: {
    agents: [{
      agent_id: 'agent-1',
      name: 'Research Agent',
      description: null,
      is_public: true,
      owned_by_viewer: false,
      status: { kind: 'idle', last_activity_at: '2026-08-24T12:00:00Z', started_at: null },
      running_count_bucket: '0',
    }],
    error: null,
    setVisibility: vi.fn(),
    setTauriFocused: vi.fn(),
    onFetchSuccess: vi.fn(),
    onFetchError: vi.fn(),
    onRateLimited: vi.fn(),
    computeInterval: () => Infinity,
    computeRunningCount: () => 0,
    lastTrayCount: 0,
  },
  configState: {
    agents: [{
      agent_id: 'agent-1',
      name: 'Research Agent',
      created_by: 'owner-1',
      agent_framework: 'codex_cli',
      model: 'gpt-5.5',
      bound_channels: ['lark', 'telegram'],
    }],
    refreshAgents: vi.fn(),
    userId: 'owner-1',
    displayName: 'Owner',
    setAgentId: vi.fn(),
    setAgents: vi.fn(),
  },
  teamsState: {
    teams: [{
      team: {
        team_id: 'team-1',
        owner_user_id: 'owner-1',
        name: 'Research Team',
        description: 'Coordinates the research workflow.',
        color: '#4F86F7',
        source: 'local',
      },
      member_agent_ids: ['agent-1'],
    }],
    refresh: vi.fn(),
    updateTeam: vi.fn(),
    deleteTeam: vi.fn(),
  },
  chatState: {
    setActiveAgent: vi.fn(),
    clearAgent: vi.fn(),
    requestHistoryRefresh: vi.fn(),
    requestWorkspaceRefresh: vi.fn(),
  },
}));

const copy: Record<string, string> = {
  'sidebar.agents': 'Agents',
  'sidebar.teams': 'Squads',
  'pages.dashboard.agentsCount': '1 agent',
  'pages.dashboard.newAgent': 'New agent',
  'pages.dashboard.colFramework': 'Framework',
  'pages.dashboard.colModel': 'Model',
  'pages.dashboard.colChannels': 'Channels',
  'pages.dashboard.colLastActive': 'Last active',
  'pages.dashboard.chat': 'Chat',
  'pages.manageAgents.openChat': 'Open chat',
  'pages.dashboard.membersCount': '1 agent',
  'pages.dashboard.teamProfileNoDescription': 'No team description.',
  'pages.manageAgents.colNameId': 'Name / ID',
  'pages.manageAgents.searchPlaceholder': 'Search agents…',
  'pages.dashboard.statRunning': 'Running',
  'pages.dashboard.statQueued': 'Queued',
  'pages.dashboard.statErrors': 'Errors',
  'pages.dashboard.statCostToday': 'Cost today',
  'pages.manageAgents.filterAll': 'All agents',
  'pages.manageAgents.addToTeam': 'Add to team',
  'pages.manageAgents.removeFromTeam': 'Remove from team',
  'pages.manageAgents.delete': 'Delete',
  'pages.manageAgents.summary': '1 shown · 0 selected · 1 total',
  'pages.dashboard.memberRuntimeLabel': 'Runtime',
  'pages.dashboard.memberModelLabel': 'Model',
  'pages.dashboard.memberOwnerLabel': 'Owner',
  'pages.dashboard.memberPrivate': 'Private',
  'pages.dashboard.memberPublic': 'Public',
  'pages.dashboard.agentProfileNoDescription': 'No description.',
  'dashboard.summary.chip.running': 'Running',
  'dashboard.summary.chip.idle': 'Idle',
  'dashboard.summary.chip.error': 'Error',
  'dashboard.summary.chip.blocked': 'Blocked',
  'dashboard.summary.chip.paused': 'Paused',
};

vi.mock('react-i18next', () => ({
  useTranslation: () => ({
    t: (key: string) => copy[key] ?? key,
    i18n: { language: 'en' },
  }),
}));
vi.mock('@/stores/dashboardStore', () => ({
  useDashboardStore: Object.assign(
    (selector: (state: typeof dashboardState) => unknown) => selector(dashboardState),
    { getState: () => dashboardState, setState: vi.fn() },
  ),
}));
vi.mock('@/stores', () => ({
  useConfigStore: () => configState,
  useTeamsStore: () => teamsState,
  useChatStore: () => chatState,
}));
vi.mock('@/hooks', () => ({ useCreateAgent: () => ({ createAgent: vi.fn(), creating: false }) }));
vi.mock('@/lib/api', () => ({
  api: { getDashboardStatus: vi.fn().mockResolvedValue({ success: true, agents: [] }) },
}));
vi.mock('@/lib/tauri', () => ({ setTrayBadge: vi.fn(), listenTauri: vi.fn().mockResolvedValue(null) }));
vi.mock('@/components/ui', () => ({
  Button: ({ children, ...props }: React.ButtonHTMLAttributes<HTMLButtonElement>) => <button {...props}>{children}</button>,
  ScrollArea: ({ children }: { children: React.ReactNode }) => <div>{children}</div>,
  useConfirm: () => ({ confirm: vi.fn(), alert: vi.fn(), dialog: null }),
}));
vi.mock('@/components/nm', () => ({
  BracketSectionLabel: ({ children }: { children: React.ReactNode }) => <div>{children}</div>,
  BracketEmptyState: () => <div />,
  KPITile: ({ label }: { label: string }) => <div>{label}</div>,
  RingAvatar: ({ species }: { species?: string }) => <div data-nm="ring-avatar" data-species={species} />,
  GroupAvatar: ({ members, label }: { members: Array<{ species: string }>; label?: string }) => (
    <div data-nm="group-avatar" data-species={members.map((member) => member.species).join(',')}>{label}</div>
  ),
  StatusDot: ({ status }: { status: string }) => <div data-nm="status-dot" data-status={status} />,
}));
vi.mock('@/components/ui/tooltip', () => ({
  TooltipProvider: ({ children }: { children: React.ReactNode }) => <>{children}</>,
  Tooltip: ({ children }: { children: React.ReactNode }) => <>{children}</>,
  TooltipTrigger: ({ children }: { children: React.ReactNode }) => <>{children}</>,
  TooltipContent: ({ children }: { children: React.ReactNode }) => <div role="tooltip">{children}</div>,
}));
vi.mock('@/components/dashboard/AttentionBanners', () => ({ AttentionBanners: () => null }));
vi.mock('@/components/dashboard/SessionSection', () => ({ SessionSection: () => null }));
vi.mock('@/components/dashboard/JobsSection', () => ({ JobsSection: () => null }));
vi.mock('@/components/dashboard/QueueBar', () => ({ QueueBar: () => null }));
vi.mock('@/components/dashboard/Sparkline', () => ({ Sparkline: () => null }));
vi.mock('@/components/dashboard/RecentFeed', () => ({ RecentFeed: () => null }));
vi.mock('@/components/dashboard/MetricsRow', () => ({ MetricsRow: () => null }));
vi.mock('@/components/teams/TeamManagementModal', () => ({ TeamManagementModal: () => null }));
vi.mock('@/components/teams/ClearTeamDataDialog', () => ({ ClearTeamDataDialog: () => null }));
vi.mock('@/components/layout/AgentRowMenu', () => ({
  AgentRowMenu: () => <div data-testid="agent-row-menu" />,
}));
vi.mock('@/components/layout/TeamRowMenu', () => ({ TeamRowMenu: () => null }));
vi.mock('@/components/layout/ClearAgentDataDialog', () => ({ ClearAgentDataDialog: () => null }));
vi.mock('@/components/layout/EditAgentDialog', () => ({ EditAgentDialog: () => null }));

import DashboardPage from '../DashboardPage';

function LocationProbe() {
  return <output data-testid="location-probe">{useLocation().pathname}</output>;
}

describe('DashboardPage streamlined information architecture', () => {
  test('opens the agent profile instead of expanding the directory row', () => {
    render(
      <MemoryRouter initialEntries={['/app/dashboard']}>
        <DashboardPage />
        <LocationProbe />
      </MemoryRouter>,
    );

    const row = screen.getByTestId('dash-row-agent-1');
    fireEvent.click(row);

    expect(screen.getByTestId('location-probe').textContent).toBe('/app/agents/agent-1');
  });

  test('uses an em dash when an agent has no team', () => {
    const memberAgentIds = teamsState.teams[0].member_agent_ids;
    teamsState.teams[0].member_agent_ids = [];

    try {
      render(<MemoryRouter><DashboardPage /></MemoryRouter>);

      expect(screen.getByTestId('teams-agent-1').textContent).toBe('—');
    } finally {
      teamsState.teams[0].member_agent_ids = memberAgentIds;
    }
  });

  test('keeps search and creation prominent without legacy bulk-management chrome', () => {
    render(<MemoryRouter><DashboardPage /></MemoryRouter>);

    expect(screen.getByRole('heading', { name: 'Agents' })).toBeTruthy();
    expect(screen.getByRole('button', { name: 'New agent' })).toBeTruthy();
    expect(screen.getByPlaceholderText('Search agents…')).toBeTruthy();
    expect(screen.getByText('Framework')).toBeTruthy();
    expect(screen.getByText('Model')).toBeTruthy();
    expect(screen.getByText('Channels')).toBeTruthy();
    expect(screen.getByText('Last active')).toBeTruthy();
    expect(screen.getByTitle('2026-08-24T12:00:00Z')).toBeTruthy();
    expect(screen.getByText('Codex')).toBeTruthy();
    expect(screen.getByText('gpt-5.5')).toBeTruthy();
    expect(screen.getByTestId('framework-agent-1').querySelector('svg, img')).toBeTruthy();
    expect(screen.getByTestId('model-agent-1').querySelector('svg, img')).toBeTruthy();
    expect(screen.getByRole('button', { name: 'Open chat' }).textContent).toContain('Chat');
    expect(screen.queryByTestId('agent-row-menu')).toBeNull();
    expect(screen.getByTestId('channels-agent-1').querySelectorAll('[data-channel]').length).toBe(2);
    const teamCell = screen.getByTestId('teams-agent-1');
    expect(teamCell.className).toContain('-space-x-2');
    const teamAvatar = screen.getByTestId('team-avatar-trigger-team-1').querySelector('[data-nm="group-avatar"]');
    expect(teamAvatar).toBeTruthy();
    expect(teamAvatar?.getAttribute('data-species')).toBe('carbon,silicon');
    expect(screen.getByRole('button', { name: 'Research Team' }).textContent).toBe('RT');
    expect(screen.getByText('Coordinates the research workflow.')).toBeTruthy();
    expect(screen.getByText('1 agent')).toBeTruthy();
    expect(screen.getByTestId('agent-directory-table').className).not.toContain('border');
    expect(screen.getByTestId('agent-directory-table').querySelector('[class*="border-b"], [class*="border-t"]')).toBeNull();
    expect(screen.getByText('Name / ID').className).not.toContain('font-mono');
    expect(screen.getByText('agent-1').className).not.toContain('font-mono');
    expect(screen.getByText('gpt-5.5').className).not.toContain('font-mono');
    expect(screen.queryByRole('button', { name: 'Squads' })).toBeNull();
    expect(screen.queryByRole('checkbox')).toBeNull();

    for (const removedLabel of [
      'Running',
      'Queued',
      'Errors',
      'Cost today',
      'All agents',
      'Add to team',
      'Remove from team',
      'Delete',
      '1 shown · 0 selected · 1 total',
    ]) {
      expect(screen.queryByText(removedLabel)).toBeNull();
    }
  });
});

describe('DashboardPage Teams tab — agent-list-style member avatars', () => {
  test('caps visible member avatars, overflows the rest, and hovers/clicks like the Agents table', () => {
    const originalAgents = configState.agents;
    const originalMembers = teamsState.teams[0].member_agent_ids;
    configState.agents = [
      ...originalAgents,
      {
        agent_id: 'agent-2',
        name: 'Growth Agent',
        description: 'Runs 小红书 growth campaigns.',
        created_by: 'owner-1',
        agent_framework: 'claude_code',
        model: 'claude-5',
        is_public: false,
        bound_channels: [],
      },
      { agent_id: 'agent-3', name: 'Ops Agent', created_by: 'someone-else', bound_channels: [] },
      { agent_id: 'agent-4', name: 'Support Agent', created_by: 'owner-1', bound_channels: [] },
      { agent_id: 'agent-5', name: 'Sales Agent', created_by: 'owner-1', bound_channels: [] },
    ];
    teamsState.teams[0].member_agent_ids = ['agent-1', 'agent-2', 'agent-3', 'agent-4', 'agent-5'];

    try {
      render(
        <MemoryRouter initialEntries={['/app/dashboard?view=teams']}>
          <DashboardPage />
          <LocationProbe />
        </MemoryRouter>,
      );

      // Members column now renders individual agent avatars, capped at 2 with an overflow badge.
      expect(screen.getByTestId('team-member-avatar-agent-1')).toBeTruthy();
      expect(screen.getByTestId('team-member-avatar-agent-2')).toBeTruthy();
      expect(screen.queryByTestId('team-member-avatar-agent-3')).toBeNull();
      expect(screen.queryByTestId('team-member-avatar-agent-4')).toBeNull();
      expect(screen.getByTestId('team-member-avatars-overflow')).toBeTruthy();

      // Hover-card content for the fully-configured, private member is present in the DOM.
      expect(screen.getByText('Growth Agent')).toBeTruthy();
      expect(screen.getByText('Runs 小红书 growth campaigns.')).toBeTruthy();
      expect(screen.getByText('Claude Code')).toBeTruthy();
      expect(screen.getByText('claude-5')).toBeTruthy();

      // The old "Manage" button is gone — only the chat icon action remains.
      expect(screen.queryByRole('button', { name: /manage/i })).toBeNull();

      // Clicking a member avatar navigates straight to that agent's own Profile.
      fireEvent.click(screen.getByTestId('team-member-avatar-agent-2'));
      expect(screen.getByTestId('location-probe').textContent).toBe('/app/agents/agent-2');
    } finally {
      configState.agents = originalAgents;
      teamsState.teams[0].member_agent_ids = originalMembers;
    }
  });

  test('shows the team creator as a carbon (human) avatar + name, matching the Leader column', () => {
    render(<MemoryRouter initialEntries={['/app/dashboard?view=teams']}><DashboardPage /></MemoryRouter>);

    const createdByCell = screen.getByTestId('team-created-by');
    const createdByAvatar = createdByCell.querySelector('[data-nm="ring-avatar"]');
    expect(createdByAvatar).toBeTruthy();
    expect(createdByAvatar?.getAttribute('data-species')).toBe('carbon');
    expect(createdByCell.textContent).toContain('Owner');
  });

  test('renders the Team name avatar with the same GroupAvatar used in the Messenger team row, replacing the plain color dot', () => {
    render(<MemoryRouter initialEntries={['/app/dashboard?view=teams']}><DashboardPage /></MemoryRouter>);

    const teamAvatar = screen.getByTestId('team-avatar-team-1').querySelector('[data-nm="group-avatar"]');
    expect(teamAvatar?.textContent).toBe('RT');
    expect(teamAvatar?.getAttribute('data-species')).toBe('carbon,silicon');
  });
});
