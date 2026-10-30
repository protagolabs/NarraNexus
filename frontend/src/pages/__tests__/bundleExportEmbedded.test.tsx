/**
 * @file_name: bundleExportEmbedded.test.tsx
 * @description: BundleExportPage's two forms.
 *  - embedded (Dashboard "Export" tab): no standalone title cluster (back arrow
 *    + Package + h1) — the Dashboard header/tab already name the page.
 *  - standalone (/app/bundle/export): keeps the chrome, and its back/cancel now
 *    return to the ORIGIN (navigate(-1)), falling back to the dashboard export
 *    tab when the route was opened cold (no history). Reverting any of these
 *    turns a case red.
 */
import type React from 'react';
import { describe, it, expect, vi } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import { MemoryRouter, Routes, Route } from 'react-router-dom';

const storeState = vi.hoisted(() => ({ agents: [] as Array<{ agent_id: string; name: string; created_by: string }> }));

vi.mock('@/stores', () => ({
  useConfigStore: () => ({ agents: storeState.agents, userId: 'u1' }),
  useTeamsStore: () => ({ teams: [], refresh: vi.fn() }),
}));
type MockProps = { children?: React.ReactNode; onClick?: () => void };
vi.mock('@/components/ui', () => ({
  Button: ({ children, onClick }: MockProps) => <button onClick={onClick}>{children}</button>,
  useConfirm: () => ({ dialog: null, alert: vi.fn(), confirm: vi.fn() }),
}));
vi.mock('@/components/nm', () => ({
  BracketSectionLabel: ({ children }: MockProps) => <div>{children}</div>,
}));
vi.mock('@/lib/api', () => ({
  api: {
    exportBundle: vi.fn(),
    getChatHistory: vi.fn().mockResolvedValue({ success: true, narratives: [] }),
    getJobs: vi.fn().mockResolvedValue({ success: true, jobs: [] }),
    getSocialNetworkList: vi.fn().mockResolvedValue({ success: true, entities: [] }),
    listFiles: vi.fn().mockResolvedValue({ success: true, files: [] }),
    listSkillArchives: vi.fn().mockResolvedValue({ success: true, archives: [] }),
    listSkills: vi.fn().mockResolvedValue({ success: true, skills: [] }),
    previewArtifacts: vi.fn().mockResolvedValue({ success: true, artifacts: [] }),
    previewBusChannels: vi.fn().mockResolvedValue({ success: true, channels: [] }),
    previewMcps: vi.fn().mockResolvedValue({ success: true, mcps: [] }),
    uploadSkillArchive: vi.fn(),
  },
}));

import BundleExportPage from '../BundleExportPage';
import { api } from '@/lib/api';

// Renders the standalone route with markers for the two exit destinations so a
// real navigate(-1) / navigate('/app/dashboard?tab=export') is observable.
function renderStandalone(entries: string[], index?: number) {
  return render(
    <MemoryRouter initialEntries={entries} initialIndex={index}>
      <Routes>
        <Route path="/app/bundle/export" element={<BundleExportPage />} />
        <Route path="/app/dashboard" element={<div>dashboard-marker</div>} />
        <Route path="/app/teams/:id" element={<div>team-marker</div>} />
      </Routes>
    </MemoryRouter>,
  );
}

describe('BundleExportPage embedded mode (#1)', () => {
  it('standalone renders the title cluster (back arrow + h1)', () => {
    render(<MemoryRouter><BundleExportPage /></MemoryRouter>);
    expect(screen.getByLabelText('Back')).toBeInTheDocument();
    expect(screen.getByRole('heading', { name: /export bundle/i })).toBeInTheDocument();
  });

  it('embedded hides the whole standalone title cluster', () => {
    render(<MemoryRouter><BundleExportPage embedded /></MemoryRouter>);
    expect(screen.queryByLabelText('Back')).toBeNull();
    expect(screen.queryByRole('heading', { name: /export bundle/i })).toBeNull();
  });
});

describe('BundleExportPage standalone exit (#4)', () => {
  it('cold-opened (no history) → Cancel falls back to the dashboard export tab', () => {
    renderStandalone(['/app/bundle/export']); // single entry → location.key === 'default'
    fireEvent.click(screen.getByRole('button', { name: /cancel/i }));
    expect(screen.getByText('dashboard-marker')).toBeInTheDocument();
  });

  it('reached from an origin → Cancel returns there (navigate(-1))', () => {
    renderStandalone(['/app/teams/t1', '/app/bundle/export'], 1);
    fireEvent.click(screen.getByRole('button', { name: /cancel/i }));
    expect(screen.getByText('team-marker')).toBeInTheDocument();
  });
});

describe('BundleExportPage ?agents= quick-launch', () => {
  it("takes only the viewer's own agents from the link", async () => {
    // The query is link-controlled. Unsafe ids would be sent as tenant-boundary
    // violations, and well-formed foreign ids — any number of them — would fire
    // five owner-gated preview reads each into 403/404s (the edge bans on 20).
    // A team only ever holds its owner's agents, so nothing legitimate is lost.
    storeState.agents = [
      { agent_id: 'agent_1', name: 'Mine', created_by: 'u1' },
      { agent_id: 'public_1', name: 'Theirs', created_by: 'stranger' },
    ];
    vi.mocked(api.previewBusChannels).mockClear();
    vi.mocked(api.previewBusChannels).mockResolvedValue({ success: true, channels: [] } as never);
    renderStandalone(['/app/bundle/export?agents=..,a%2Fb,.x,foreign_1,public_1,agent_1']);
    await vi.waitFor(() => expect(api.previewBusChannels).toHaveBeenCalled());
    for (const [ids] of vi.mocked(api.previewBusChannels).mock.calls) {
      expect(ids).toEqual(['agent_1']);
    }
    storeState.agents = [];
  });
});
