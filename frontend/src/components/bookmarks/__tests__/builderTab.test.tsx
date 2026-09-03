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
import { render, waitFor, within } from '@testing-library/react';
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

/**
 * Scoped queries, never the global `screen`: these tests each mount the panel,
 * and a global query counts leftovers from a sibling render if cleanup has not
 * run yet. That made `getAllByText('Optional')` intermittently find four.
 */
// The panel is React.lazy'd. On a COLD run vitest still has to transform that
// chunk, which overruns waitFor's 1s default — the first test in the file
// failed intermittently on "Unable to find ... Identity" while warm runs
// passed. Waiting longer is the honest fix; the assertion is unchanged.
const LAZY_MOUNT_TIMEOUT = 8000;

function renderBuilder() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const { container } = render(
    <QueryClientProvider client={qc}>
      <BookmarkPanelHost tab="builder" agentId="agent_test" />
    </QueryClientProvider>,
  );
  return within(container);
}

describe('builder tab', () => {
  test('is registered, so the drawer can label and switch to it', () => {
    expect(ALL_TABS.some((t) => t.id === 'builder')).toBe(true);
  });

  test('renders the configuration panel rather than an empty drawer', async () => {
    const view = renderBuilder();
    // Lazy chunk + Suspense: wait for the real panel, not the fallback.
    await waitFor(
      () => {
        expect(view.getByText('Identity')).toBeInTheDocument();
      },
      { timeout: LAZY_MOUNT_TIMEOUT },
    );
    // Per the Owner's 2026-09-03 reference: name only, the instruction box
    // named after the field it writes, plus real Skills and Channels.
    expect(view.getByText('Name')).toBeInTheDocument();
    expect(view.getByText('Awareness')).toBeInTheDocument();
    expect(view.getByText('Skills')).toBeInTheDocument();
    expect(view.getByText('Channels')).toBeInTheDocument();
    expect(view.getByTestId('skills-section')).toBeInTheDocument();
    expect(view.getByTestId('channels-section')).toBeInTheDocument();
    expect(view.getByRole('button', { name: 'Done' })).toBeInTheDocument();
  });

  test('marks Skills and Channels as optional, and Identity as not', async () => {
    // Both are genuinely skippable; labelling them stops the panel reading as
    // a checklist the user has to finish before the agent works.
    const view = renderBuilder();
    await waitFor(
      () => {
        expect(view.getByText('Identity')).toBeInTheDocument();
      },
      { timeout: LAZY_MOUNT_TIMEOUT },
    );
    expect(view.getAllByText('Optional')).toHaveLength(2);
  });

  test('drops the avatar and description the reference removed', async () => {
    const view = renderBuilder();
    await waitFor(
      () => {
        expect(view.getByText('Identity')).toBeInTheDocument();
      },
      { timeout: LAZY_MOUNT_TIMEOUT },
    );
    // No avatar affordance (the project has no agent-avatar capability) and no
    // description field (machine-facing copy — the conversation writes it and
    // Agent Profile shows it).
    expect(view.queryByText('Description')).not.toBeInTheDocument();
    expect(view.queryByText(/avatar/i)).not.toBeInTheDocument();
  });
});
