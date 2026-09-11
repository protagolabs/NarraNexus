/**
 * @file_name: chatViewDrawerSwitcher.test.tsx
 * @date: 2026-09-11
 * @description: Single-chat wiring of the drawer's title switcher.
 *
 * Owner-required (reinstated 2026-09-11): ChatView must hand the drawer its
 * switcher registry, so a drawer opened on Artifacts can switch itself to
 * another panel from its title. The component-level behaviour lives in
 * bookmarks/__tests__/drawerPanelSwitcher.test.tsx; this pins that the
 * single-chat caller actually wires it (drop the three props → red).
 */

import { describe, test, expect, vi, beforeEach } from 'vitest';
import { act, render, screen, fireEvent } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';

vi.mock('@/components/chat', () => ({ ChatPanel: () => <div data-testid="chat-panel" /> }));
vi.mock('@/components/chat/WakingOverlay', () => ({ WakingOverlay: () => null }));
vi.mock('@/components/onboarding/GuideAgentCoachmark', () => ({ GuideAgentCoachmark: () => null }));
vi.mock('@/components/onboarding/MigrationGuide', () => ({ MigrationGuide: () => null }));
vi.mock('@/components/cost/CostPopover', () => ({ CostPopover: () => null }));
vi.mock('@/hooks/useBookmarkSignals', () => ({ useBookmarkSignals: () => undefined }));
vi.mock('@/components/bookmarks/BookmarkPanelHost', () => ({
  BookmarkPanelHost: ({ tab }: { tab: string }) => <div data-testid={`panel-${tab}`} />,
}));
vi.mock('@/hooks', async (orig) => ({
  ...(await orig<typeof import('@/hooks')>()),
  useAutoRefresh: () => ({ refreshAll: vi.fn() }),
}));

import { ChatView } from '../MainLayout';
import { DRAWER_FIRST_RUN_KEY, DRAWER_OPENED_ONCE_KEY } from '../drawerLayout';
import { useConfigStore, useArtifactStore, useUIStore } from '@/stores';

describe('ChatView — drawer title switcher', () => {
  beforeEach(() => {
    window.localStorage.clear();
    // Skip the first-run auto-open so the test controls which panel is open.
    window.localStorage.setItem(DRAWER_FIRST_RUN_KEY, '1');
    useConfigStore.setState({ agentId: 'agent_1', userId: 'user_1' });
    useArtifactStore.setState({ loadPinned: vi.fn() } as never);
  });

  test('a drawer opened on Artifacts switches to Jobs from its title', () => {
    render(
      <MemoryRouter>
        <ChatView />
      </MemoryRouter>,
    );
    act(() => useUIStore.getState().openPanel('artifacts'));
    expect(screen.getByTestId('panel-artifacts')).toBeTruthy();

    fireEvent.click(screen.getByRole('button', { name: /switch panel/i }));
    expect(
      screen.getByTestId('drawer-switcher-item-artifacts').getAttribute('aria-checked'),
    ).toBe('true');
    // No studio on this agent → the conditional builder tab is not offered.
    expect(screen.queryByTestId('drawer-switcher-item-builder')).toBeNull();

    // The switcher goes through the same uiStore panel funnel as every other
    // entry: that funnel's bookkeeping (the opened-once mark) must run for it.
    window.localStorage.removeItem(DRAWER_OPENED_ONCE_KEY);
    fireEvent.click(screen.getByTestId('drawer-switcher-item-jobs'));
    expect(screen.queryByTestId('panel-artifacts')).toBeNull();
    expect(screen.getByTestId('panel-jobs')).toBeTruthy();
    expect(window.localStorage.getItem(DRAWER_OPENED_ONCE_KEY)).toBe('1');
  });
});
