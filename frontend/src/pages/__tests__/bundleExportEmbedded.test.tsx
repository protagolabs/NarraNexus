/**
 * @file_name: bundleExportEmbedded.test.tsx
 * @description: Req #1 — BundleExportPage embeds inside the Dashboard "Export"
 * tab. In `embedded` mode it must NOT render its own standalone back-to-settings
 * header arrow (the tab rail is the chrome); standalone mode keeps it. Reverting
 * the `embedded` gate turns one of these red.
 */
import type React from 'react';
import { describe, it, expect, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';

vi.mock('@/stores', () => ({
  useConfigStore: () => ({ agents: [], userId: 'u1' }),
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

const renderPage = (embedded: boolean) =>
  render(
    <MemoryRouter initialEntries={['/app/dashboard?tab=export']}>
      <BundleExportPage embedded={embedded} />
    </MemoryRouter>,
  );

describe('BundleExportPage embedded mode (#1)', () => {
  it('standalone renders the back-to-settings header arrow', () => {
    renderPage(false);
    expect(screen.getByLabelText('Back to settings')).toBeInTheDocument();
  });

  it('embedded hides the standalone back-to-settings header arrow', () => {
    renderPage(true);
    expect(screen.queryByLabelText('Back to settings')).toBeNull();
  });
});
