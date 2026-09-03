/**
 * The drawer-side contract for the creation studio's tab.
 *
 * Two things this pins, both of which we got wrong once:
 *
 * 1. `requestPanel` TOGGLES. Using it to reveal the studio panel meant that
 *    entering the studio while that panel was already the open tab CLOSED it
 *    — "clicked Create with AI, nothing popped". `openPanel` must never close.
 *
 * 2. The `builder` tab has to route to a panel. A tab id BookmarkPanelHost
 *    does not handle opens an EMPTY drawer, which looks identical to "the
 *    feature is not wired up".
 *
 * The panel's own structure and behaviour live in
 * components/builder/__tests__/builderConfigPanel.test.tsx, rendered directly
 * — going through the lazy wrapper for those made them flaky under load.
 */
import { describe, test, expect, beforeEach, vi } from 'vitest';
import { render, waitFor, within } from '@testing-library/react';
import { useUIStore } from '@/stores/uiStore';
import { BookmarkPanelHost } from '../BookmarkPanelHost';
import { ALL_TABS } from '../tabs';

vi.mock('@/components/builder', () => ({
  BuilderConfigPanel: ({ agentId }: { agentId: string }) => (
    <div data-testid="builder-panel">{agentId}</div>
  ),
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

  test('routes to the configuration panel rather than an empty drawer', async () => {
    const { container } = render(<BookmarkPanelHost tab="builder" agentId="agent_test" />);
    const view = within(container);
    // Still lazy here, hence waitFor — but it is ONE assertion, so a slow
    // transform costs one retry rather than eight flaky tests.
    await waitFor(
      () => {
        expect(view.getByTestId('builder-panel')).toBeInTheDocument();
      },
      { timeout: 10000 },
    );
    expect(view.getByTestId('builder-panel')).toHaveTextContent('agent_test');
  });
});
