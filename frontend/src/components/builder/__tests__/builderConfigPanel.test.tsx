/**
 * @file_name: builderConfigPanel.test.tsx
 * @description: Structure and behaviour of the creation studio's panel.
 *
 * Renders the panel DIRECTLY rather than through BookmarkPanelHost's lazy
 * wrapper: under full-suite load the lazy chunk's transform overran waitFor
 * and these tests failed intermittently on "Unable to find ... Identity".
 * The lazy ROUTING is covered separately in
 * components/bookmarks/__tests__/builderTab.test.tsx — that is one assertion,
 * not eight.
 */
import { describe, test, expect, beforeEach, vi } from 'vitest';
import { fireEvent, render, waitFor, within } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';

vi.mock('@/lib/api', () => ({
  api: {
    getAwareness: vi.fn().mockResolvedValue({ awareness: '' }),
    updateAgent: vi.fn().mockResolvedValue({ success: true }),
    updateAwareness: vi.fn().mockResolvedValue({ success: true }),
    installMarketplaceSkill: vi.fn().mockResolvedValue({}),
    listSkills: vi.fn().mockResolvedValue({ skills: [] }),
  },
}));

// The panel embeds the drawer's own Skills and Channels sections. Stub them:
// this file defends the STUDIO panel, and mounting those two would re-test
// them plus drag in their API surface.
vi.mock('@/components/skills', () => ({
  SkillsPanel: () => <div data-testid="skills-section" />,
}));
vi.mock('@/components/awareness', () => ({
  AwarenessPanel: () => <div data-testid="channels-section" />,
}));

import { api } from '@/lib/api';
import { useUIStore } from '@/stores/uiStore';
import { isStudioOpen, openStudio, saveRecommendations } from '@/lib/builderSession';
import { BuilderConfigPanel } from '../BuilderConfigPanel';

const AGENT = 'agent_test';

function renderPanel() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const { container } = render(
    <QueryClientProvider client={qc}>
      <BuilderConfigPanel agentId={AGENT} />
    </QueryClientProvider>,
  );
  return within(container);
}

beforeEach(() => {
  window.sessionStorage.clear();
  useUIStore.getState().clearPendingPanel();
  vi.mocked(api.updateAwareness).mockClear().mockResolvedValue({ success: true } as never);
  vi.mocked(api.installMarketplaceSkill).mockClear().mockResolvedValue({} as never);
});

describe('studio panel structure', () => {
  test('shows identity, awareness and the two optional sections', () => {
    const view = renderPanel();
    expect(view.getByText('Identity')).toBeInTheDocument();
    expect(view.getByText('Name')).toBeInTheDocument();
    expect(view.getByText('Awareness')).toBeInTheDocument();
    expect(view.getByText('Skills')).toBeInTheDocument();
    expect(view.getByText('Channels')).toBeInTheDocument();
    expect(view.getByTestId('skills-section')).toBeInTheDocument();
    expect(view.getByTestId('channels-section')).toBeInTheDocument();
    expect(view.getByRole('button', { name: 'Done' })).toBeInTheDocument();
  });

  test('marks Skills and Channels optional — they are genuinely skippable', () => {
    const view = renderPanel();
    expect(view.getAllByText('Optional')).toHaveLength(2);
  });

  test('drops the avatar and description the reference removed', () => {
    // No avatar affordance (the project has no agent-avatar capability) and no
    // description field (machine-facing copy — the conversation writes it and
    // Agent Profile shows it).
    const view = renderPanel();
    expect(view.queryByText('Description')).not.toBeInTheDocument();
    expect(view.queryByText(/avatar/i)).not.toBeInTheDocument();
  });
});

describe('Done', () => {
  test('leaves the studio AND asks the drawer to close', async () => {
    // Regression: Done used to clear the studio flag only. The flag lives in
    // sessionStorage, so nothing re-rendered and the panel stayed on screen —
    // indistinguishable from "Done is broken", and clicking again repeated it.
    openStudio(AGENT);
    const view = renderPanel();

    fireEvent.click(view.getByRole('button', { name: 'Done' }));

    await waitFor(() => {
      expect(isStudioOpen(AGENT)).toBe(false);
    });
    expect(useUIStore.getState().pendingPanel).toBe('builder');
    expect(useUIStore.getState().pendingPanelMode).toBe('toggle');
  });

  test('flushes an unblurred edit instead of losing it', async () => {
    // A real browser blurs the textarea before the button's click, but nothing
    // guarantees that commit finished — and in jsdom no blur fires at all. So
    // Done awaits both commits itself.
    openStudio(AGENT);
    const view = renderPanel();

    fireEvent.change(view.getByPlaceholderText(/What this agent does/i), {
      target: { value: '## Role\nMorning brief' },
    });
    fireEvent.click(view.getByRole('button', { name: 'Done' }));

    await waitFor(() => {
      expect(api.updateAwareness).toHaveBeenCalledWith(AGENT, '## Role\nMorning brief');
    });
  });

  test('stays clickable while a skill is installing', async () => {
    // Requirement (Owner, 2026-09-03): installing/studying a skill must NOT
    // gate Done. Study runs for minutes inside the agent's workspace and keeps
    // running after the studio closes — blocking on it would strand the user.
    saveRecommendations(AGENT, { skill_ids: ['web-search'], channels: [] });
    vi.mocked(api.installMarketplaceSkill).mockReturnValue(new Promise(() => undefined) as never);
    const view = renderPanel();

    fireEvent.click(view.getByRole('button', { name: /Install/i }));
    await waitFor(() => {
      expect(view.getByRole('button', { name: /Install/i })).toBeDisabled();
    });
    expect(view.getByRole('button', { name: 'Done' })).toBeEnabled();
  });
});
