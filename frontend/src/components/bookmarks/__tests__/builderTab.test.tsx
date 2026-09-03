/**
 * Two things this pins, both of which we got wrong once:
 *
 * 1. `requestPanel` TOGGLES. Using it to reveal the studio panel meant that
 *    entering the studio while that panel was already the open tab CLOSED it
 *    — "clicked Create with AI, nothing popped". `openPanel` must never close.
 *
 * 2. The `builder` tab has to actually render. A tab id that BookmarkPanelHost
 *    does not handle opens an EMPTY drawer, which looks identical to "the
 *    feature is not wired up".
 */
import { describe, test, expect, beforeEach, vi } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { useUIStore } from '@/stores/uiStore';
import { BookmarkPanelHost } from '../BookmarkPanelHost';
import { ALL_TABS } from '../tabs';

vi.mock('@/lib/api', () => ({
  api: {
    getAwareness: vi.fn().mockResolvedValue({ awareness: '' }),
    updateAgent: vi.fn().mockResolvedValue({ success: true }),
    updateAwareness: vi.fn().mockResolvedValue({ success: true }),
    installMarketplaceSkill: vi.fn().mockResolvedValue({}),
  },
}));

// The panel embeds the drawer's own Skills and Channels sections. Stub them:
// this test defends the STUDIO panel's structure, and mounting those two
// would re-test them plus drag in their API surface.
vi.mock('@/components/skills', () => ({
  SkillsPanel: () => <div data-testid="skills-section" />,
}));
vi.mock('@/components/awareness', () => ({
  AwarenessPanel: () => <div data-testid="channels-section" />,
}));

describe('uiStore panel intent', () => {
  beforeEach(() => {
    useUIStore.getState().clearPendingPanel();
  });

  test('requestPanel asks for a toggle', () => {
    useUIStore.getState().requestPanel('builder');
    expect(useUIStore.getState().pendingPanel).toBe('builder');
    expect(useUIStore.getState().pendingPanelMode).toBe('toggle');
  });

  test('openPanel asks to END UP open — the studio must not close itself', () => {
    useUIStore.getState().openPanel('builder');
    expect(useUIStore.getState().pendingPanel).toBe('builder');
    expect(useUIStore.getState().pendingPanelMode).toBe('open');
  });

  test('clearing resets the mode so a stale intent cannot leak', () => {
    useUIStore.getState().openPanel('builder');
    useUIStore.getState().clearPendingPanel();
    expect(useUIStore.getState().pendingPanel).toBeNull();
    expect(useUIStore.getState().pendingPanelMode).toBe('toggle');
  });
});

describe('builder tab', () => {
  test('is registered, so the drawer can label and switch to it', () => {
    expect(ALL_TABS.some((t) => t.id === 'builder')).toBe(true);
  });

  test('renders the configuration panel rather than an empty drawer', async () => {
    const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    render(
      <QueryClientProvider client={qc}>
        <BookmarkPanelHost tab="builder" agentId="agent_test" />
      </QueryClientProvider>,
    );
    // Lazy chunk + Suspense: wait for the real panel, not the fallback.
    await waitFor(() => {
      expect(screen.getByText('Identity')).toBeInTheDocument();
    });
    // Per the Owner's 2026-09-03 reference: name only, the instruction box
    // named after the field it writes, plus real Skills and Channels.
    expect(screen.getByText('Name')).toBeInTheDocument();
    expect(screen.getByText('Awareness')).toBeInTheDocument();
    expect(screen.getAllByText('Skills').length).toBeGreaterThan(0);
    expect(screen.getAllByText('Channels').length).toBeGreaterThan(0);
    expect(screen.getByTestId('skills-section')).toBeInTheDocument();
    expect(screen.getByTestId('channels-section')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Done' })).toBeInTheDocument();
  });

  test('drops the avatar and description the reference removed', async () => {
    const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    render(
      <QueryClientProvider client={qc}>
        <BookmarkPanelHost tab="builder" agentId="agent_test" />
      </QueryClientProvider>,
    );
    await waitFor(() => {
      expect(screen.getByText('Identity')).toBeInTheDocument();
    });
    // No avatar affordance (the project has no agent-avatar capability) and no
    // description field (machine-facing copy — the conversation writes it and
    // Agent Profile shows it).
    expect(screen.queryByText('Description')).not.toBeInTheDocument();
    expect(screen.queryByText(/avatar/i)).not.toBeInTheDocument();
  });
});
