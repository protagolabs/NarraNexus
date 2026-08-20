/**
 * @file_name: dashboardTabs.test.tsx
 * @description: Req #1 — the Dashboard's agent/team management is a left-rail
 * master–detail with THREE tabs (Manage Agents / Team Management / Export),
 * each reachable, with Create-Agent (agents) and Create-Team (teams) buttons,
 * and ?tab=export landing on the embedded export wizard. Reverting the tabbed
 * shell or a create button turns one of these red.
 */
import { describe, it, expect, vi } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';

const { navigate, createAgent, useDashboardStore } = vi.hoisted(() => {
  const dashState = {
    agents: [],
    error: null,
    setVisibility: () => {},
    setTauriFocused: () => {},
    onFetchSuccess: () => {},
    onFetchError: () => {},
    onRateLimited: () => {},
    computeInterval: () => Infinity,
    computeRunningCount: () => 0,
    lastTrayCount: 0,
  };
  const store = ((sel: (s: unknown) => unknown) => sel(dashState)) as unknown as {
    (sel: (s: unknown) => unknown): unknown;
    getState: () => typeof dashState;
    setState: (p: unknown) => void;
  };
  store.getState = () => dashState;
  store.setState = () => {};
  return { navigate: vi.fn(), createAgent: vi.fn(), useDashboardStore: store };
});

vi.mock('react-router-dom', async () => {
  const actual = await vi.importActual<typeof import('react-router-dom')>('react-router-dom');
  return { ...actual, useNavigate: () => navigate };
});
vi.mock('@/stores/dashboardStore', () => ({ useDashboardStore }));

vi.mock('@/stores', () => ({
  useConfigStore: () => ({ agents: [], refreshAgents: vi.fn().mockResolvedValue(undefined) }),
  useTeamsStore: () => ({
    teams: [],
    refresh: vi.fn().mockResolvedValue(undefined),
    addMember: vi.fn(),
    removeMember: vi.fn(),
  }),
}));
vi.mock('@/hooks', () => ({ useCreateAgent: () => ({ creating: false, createAgent }) }));
vi.mock('@/lib/api', () => ({
  api: { getDashboardStatus: vi.fn().mockResolvedValue({ success: true, agents: [] }), deleteAgent: vi.fn() },
}));
vi.mock('@/lib/tauri', () => ({
  setTrayBadge: vi.fn().mockResolvedValue(undefined),
  listenTauri: vi.fn().mockResolvedValue(() => {}),
}));
vi.mock('../BundleExportPage', () => ({ default: () => <div>export-wizard-stub</div> }));

import DashboardPage from '../DashboardPage';

const renderAt = (path: string) =>
  render(<MemoryRouter initialEntries={[path]}><DashboardPage /></MemoryRouter>);

describe('Dashboard left-rail tabs (#1)', () => {
  it('renders three tabs, defaults to agents with a Create Agent button', () => {
    renderAt('/app/dashboard');
    expect(screen.getByRole('button', { name: /manage agents/i })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /team management/i })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /^export$/i })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /create agent/i })).toBeInTheDocument();
  });

  it('teams tab shows a Create Team button that opens the create page', () => {
    renderAt('/app/dashboard');
    fireEvent.click(screen.getByRole('button', { name: /team management/i }));
    fireEvent.click(screen.getByRole('button', { name: /create team/i }));
    expect(navigate).toHaveBeenCalledWith('/app/teams/new');
  });

  it('?tab=export lands on the embedded export wizard (lazy-loaded)', async () => {
    renderAt('/app/dashboard?tab=export');
    expect(await screen.findByText('export-wizard-stub')).toBeInTheDocument();
  });
});
