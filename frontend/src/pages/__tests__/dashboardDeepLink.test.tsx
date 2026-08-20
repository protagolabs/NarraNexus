/**
 * @file_name: dashboardDeepLink.test.tsx
 * @description: Req #1 regression — the sidebar "Export" row is a plain
 * navigate to /app/dashboard?tab=export. When the dashboard is ALREADY mounted
 * (no remount), a `?tab=` change must still switch the tab — i.e. the tab is
 * driven by an effect on searchParams, not only the useState initializer.
 * Uses REAL react-router navigation (no useNavigate mock) to exercise it.
 */
import { describe, it, expect, vi } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import { MemoryRouter, useNavigate } from 'react-router-dom';

const { useDashboardStore } = vi.hoisted(() => {
  const dashState = {
    agents: [], error: null,
    setVisibility: () => {}, setTauriFocused: () => {},
    onFetchSuccess: () => {}, onFetchError: () => {}, onRateLimited: () => {},
    computeInterval: () => Infinity, computeRunningCount: () => 0, lastTrayCount: 0,
  };
  const store = ((sel: (s: unknown) => unknown) => sel(dashState)) as unknown as {
    (sel: (s: unknown) => unknown): unknown;
    getState: () => typeof dashState;
    setState: (p: unknown) => void;
  };
  store.getState = () => dashState;
  store.setState = () => {};
  return { useDashboardStore: store };
});
vi.mock('@/stores/dashboardStore', () => ({ useDashboardStore }));
vi.mock('@/stores', () => ({
  useConfigStore: () => ({ agents: [], refreshAgents: vi.fn().mockResolvedValue(undefined) }),
  useTeamsStore: () => ({ teams: [], refresh: vi.fn().mockResolvedValue(undefined), addMember: vi.fn(), removeMember: vi.fn() }),
}));
vi.mock('@/hooks', () => ({ useCreateAgent: () => ({ creating: false, createAgent: vi.fn() }) }));
vi.mock('@/lib/api', () => ({ api: { getDashboardStatus: vi.fn().mockResolvedValue({ success: true, agents: [] }), deleteAgent: vi.fn() } }));
vi.mock('@/lib/tauri', () => ({ setTrayBadge: vi.fn().mockResolvedValue(undefined), listenTauri: vi.fn().mockResolvedValue(() => {}) }));
vi.mock('../BundleExportPage', () => ({ default: () => <div>export-wizard-stub</div> }));

import DashboardPage from '../DashboardPage';

function GoExport() {
  const navigate = useNavigate();
  return <button onClick={() => navigate('/app/dashboard?tab=export')}>go-export</button>;
}

describe('Dashboard deep-link while mounted (#1)', () => {
  it('switches to the Export tab when ?tab=export arrives without a remount', () => {
    render(
      <MemoryRouter initialEntries={['/app/dashboard']}>
        <GoExport />
        <DashboardPage />
      </MemoryRouter>,
    );
    // Starts on agents — no export wizard yet.
    expect(screen.queryByText('export-wizard-stub')).toBeNull();
    // Simulate the sidebar Export row navigating while the page stays mounted.
    fireEvent.click(screen.getByText('go-export'));
    expect(screen.getByText('export-wizard-stub')).toBeInTheDocument();
  });
});
