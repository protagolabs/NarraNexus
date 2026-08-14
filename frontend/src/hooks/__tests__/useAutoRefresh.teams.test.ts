/**
 * @file_name: useAutoRefresh.teams.test.ts
 * @description: The sidebar's team rows are kept fresh in the background.
 *
 * The room-activity mark on a team row is a comparison between a client
 * watermark and a server timestamp that arrives with the TEAM LIST. Nothing
 * refreshed that list on a timer: it was fetched once, when the sidebar found it
 * unloaded. So a room could talk for an hour and the mark would appear only on
 * the next full page reload — a feature indistinguishable, from the user's side,
 * from one that does not work.
 *
 * The second thing pinned here is the guard. The scheduler used to require a
 * selected AGENT before starting at all, which meant a user sitting in a team
 * room could have no background refresh whatsoever. Every poll that needs an
 * agent still checks for one itself.
 */
import { describe, expect, test, vi, beforeEach, afterEach } from 'vitest';
import { renderHook } from '@testing-library/react';

const teamsRefresh = vi.fn();
const refreshAgents = vi.fn();

vi.mock('@/stores', () => ({
  useTeamsStore: { getState: () => ({ refresh: teamsRefresh }) },
  useConfigStore: { getState: () => ({ refreshAgents, agents: [] }) },
  useChatStore: { getState: () => ({ isAgentStreaming: () => false }) },
  useArtifactStore: (select: (s: unknown) => unknown) => select({ loadPinned: vi.fn() }),
  usePreloadStore: () => ({
    refreshAgentInbox: vi.fn(),
    refreshJobs: vi.fn(),
    refreshAwareness: vi.fn(),
    refreshChatHistory: vi.fn(),
    refreshSocialNetwork: vi.fn(),
  }),
}));

vi.mock('@/lib/api', () => ({
  api: { getSimpleChatHistory: () => Promise.resolve({ success: true, messages: [] }) },
}));

import { useAutoRefresh } from '../useAutoRefresh';

/** Past the 30s mid tick with room to spare — if that interval ever grows past
 *  this, the test should fail rather than quietly follow it. */
const MID_WINDOW_MS = 45_000;

beforeEach(() => {
  teamsRefresh.mockReset();
  refreshAgents.mockReset();
  vi.useFakeTimers();
});

afterEach(() => {
  vi.useRealTimers();
});

describe('background refresh keeps the team rows current', () => {
  test('the mid tick refreshes teams', () => {
    renderHook(() => useAutoRefresh({ agentId: 'a1', userId: 'usr_1' }));

    vi.advanceTimersByTime(MID_WINDOW_MS);

    expect(teamsRefresh).toHaveBeenCalled();
  });

  test('teams refresh even with no agent selected', () => {
    // A user whose window is on a team room. Every agent-scoped poll below still
    // guards on an agent id of its own.
    renderHook(() => useAutoRefresh({ agentId: '', userId: 'usr_1' }));

    vi.advanceTimersByTime(MID_WINDOW_MS);

    expect(teamsRefresh).toHaveBeenCalled();
    expect(refreshAgents).not.toHaveBeenCalled();
  });

  test('nothing polls without a user', () => {
    renderHook(() => useAutoRefresh({ agentId: 'a1', userId: '' }));

    vi.advanceTimersByTime(MID_WINDOW_MS);

    expect(teamsRefresh).not.toHaveBeenCalled();
  });

  test('a hidden tab polls nothing', () => {
    // The whole point of the visibility guard: zero requests while the tab is in
    // the background. A team refresh added past it would reopen that.
    const spy = vi.spyOn(document, 'hidden', 'get').mockReturnValue(true);
    try {
      renderHook(() => useAutoRefresh({ agentId: 'a1', userId: 'usr_1' }));
      vi.advanceTimersByTime(MID_WINDOW_MS);
      expect(teamsRefresh).not.toHaveBeenCalled();
    } finally {
      spy.mockRestore();
    }
  });

  test('returning to the tab refreshes teams immediately', () => {
    // Coming back is exactly when the user wants to know what happened while
    // they were gone; waiting up to 30s for the next tick shows a stale sidebar
    // at the one moment it is being read.
    renderHook(() => useAutoRefresh({ agentId: 'a1', userId: 'usr_1' }));
    teamsRefresh.mockReset();

    document.dispatchEvent(new Event('visibilitychange'));

    expect(teamsRefresh).toHaveBeenCalled();
  });
});
