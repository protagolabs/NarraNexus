/**
 * @file_name: useAutoRefresh.bgReplies.test.ts
 * @author: Bin Liang
 * @date: 2026-10-30
 * @description: The background "agent replied to you" detection makes one /chat-latest call per 200 agents per tick, never one chat-history call per agent.
 *
 * It used to call simple-chat-history(include='chat') for every agent every 15s
 * — a request fan-out that grew with the agent count. /chat-latest returns the
 * same signal (the newest row of each agent's conversation tab, computed by the
 * same server-side projection) for a whole list. The toast/badge rules are
 * unchanged: first sighting only records, a newer timestamp on a non-active
 * agent raises one toast, live-streaming agents are skipped.
 */
import { afterEach, beforeEach, describe, expect, test, vi } from 'vitest';
import { renderHook } from '@testing-library/react';

const BG_MESSAGE_INTERVAL = 15_000;

const h = vi.hoisted(() => {
  const state = {
    agents: [] as Array<{ agent_id: string; name: string }>,
    latest: {} as Record<string, string>,
    streaming: new Set<string>(),
    chat: { completedAgentIds: [] as string[], toastQueue: [] as Array<{ agentId: string }> },
  };
  const getChatLatest = vi.fn(async (ids: string[]) => ({
    success: true,
    latest: Object.fromEntries(ids.filter((id) => id in state.latest).map((id) => [id, state.latest[id]])),
  }));
  const getSimpleChatHistory = vi.fn();
  return { state, getChatLatest, getSimpleChatHistory };
});

vi.mock('@/stores', () => ({
  useTeamsStore: { getState: () => ({ refresh: vi.fn(async () => {}), teams: [] }) },
  useConfigStore: { getState: () => ({ refreshAgents: vi.fn(), agents: h.state.agents }) },
  useChatStore: {
    getState: () => ({ ...h.state.chat, isAgentStreaming: (aid: string) => h.state.streaming.has(aid) }),
    setState: (fn: (s: typeof h.state.chat) => Partial<typeof h.state.chat>) => {
      Object.assign(h.state.chat, fn(h.state.chat));
    },
  },
  useArtifactStore: (select: (s: unknown) => unknown) => select({ loadPinned: vi.fn() }),
  usePreloadStore: () => ({
    refreshAgentInbox: vi.fn(), refreshJobs: vi.fn(), refreshAwareness: vi.fn(),
    refreshChatHistory: vi.fn(), refreshSocialNetwork: vi.fn(),
  }),
}));
vi.mock('@/lib/api', () => ({
  api: { getChatLatest: h.getChatLatest, getSimpleChatHistory: h.getSimpleChatHistory },
}));

import { useAutoRefresh } from '../useAutoRefresh';

const tick = () => vi.advanceTimersByTimeAsync(BG_MESSAGE_INTERVAL);
const agents = (...ids: string[]) => ids.map((id) => ({ agent_id: id, name: id.toUpperCase() }));

beforeEach(() => {
  vi.useFakeTimers();
  h.getChatLatest.mockClear();
  h.getSimpleChatHistory.mockClear();
  h.state.agents = [];
  h.state.latest = {};
  h.state.streaming.clear();
  h.state.chat.completedAgentIds = [];
  h.state.chat.toastQueue = [];
});
afterEach(() => vi.useRealTimers());

describe('background reply detection', () => {
  test('250 agents cost two /chat-latest calls a tick and zero per-agent history calls', async () => {
    h.state.agents = agents(...Array.from({ length: 250 }, (_, i) => `a${i}`));
    renderHook(() => useAutoRefresh({ agentId: 'a0', userId: 'usr_1' }));
    await tick();
    expect(h.getChatLatest).toHaveBeenCalledTimes(2);
    expect(h.getChatLatest.mock.calls[0][0]).toHaveLength(200);
    expect(h.getChatLatest.mock.calls[1][0]).toHaveLength(50);
    expect(h.getSimpleChatHistory).not.toHaveBeenCalled();
  });

  test('a newer reply on a non-active agent raises one toast; the first sighting only records', async () => {
    h.state.agents = agents('a1', 'a2');
    h.state.latest = { a1: '2026-10-30T01:00:00', a2: '2026-10-30T01:00:00' };
    renderHook(() => useAutoRefresh({ agentId: 'a2', userId: 'usr_1' }));
    await tick();
    expect(h.state.chat.toastQueue).toEqual([]);
    h.state.latest = { a1: '2026-10-30T02:00:00', a2: '2026-10-30T02:00:00' };
    await tick();
    expect(h.state.chat.toastQueue.map((t) => t.agentId)).toEqual(['a1']); // a2 is on screen
    expect(h.state.chat.completedAgentIds).toEqual(['a1']);
    await tick();
    expect(h.state.chat.toastQueue).toHaveLength(1); // unchanged timestamp: no repeat
  });

  test('a streaming agent is not polled, and is re-checked once its stream ends', async () => {
    h.state.agents = agents('a1');
    h.state.latest = { a1: '2026-10-30T01:00:00' };
    renderHook(() => useAutoRefresh({ agentId: 'other', userId: 'usr_1' }));
    await tick();
    h.state.latest = { a1: '2026-10-30T02:00:00' };
    h.state.streaming.add('a1');
    h.getChatLatest.mockClear();
    await tick();
    expect(h.getChatLatest).not.toHaveBeenCalled();
    expect(h.state.chat.toastQueue).toEqual([]);
    h.state.streaming.delete('a1');
    await tick();
    expect(h.state.chat.toastQueue.map((t) => t.agentId)).toEqual(['a1']);
  });

  test('an agent with nothing in its conversation tab is skipped until it has a row', async () => {
    h.state.agents = agents('a1');
    renderHook(() => useAutoRefresh({ agentId: 'other', userId: 'usr_1' }));
    await tick();
    h.state.latest = { a1: '2026-10-30T02:00:00' };
    await tick();
    expect(h.state.chat.toastQueue).toEqual([]); // first real sighting only records
  });

  test('a failed call is retried on the next tick without toasting', async () => {
    h.state.agents = agents('a1');
    h.state.latest = { a1: '2026-10-30T01:00:00' };
    renderHook(() => useAutoRefresh({ agentId: 'other', userId: 'usr_1' }));
    await tick();
    const warn = vi.spyOn(console, 'warn').mockImplementation(() => {});
    h.getChatLatest.mockRejectedValueOnce(new Error('offline'));
    await tick();
    h.state.latest = { a1: '2026-10-30T02:00:00' };
    await tick();
    expect(h.state.chat.toastQueue.map((t) => t.agentId)).toEqual(['a1']);
    expect(warn).toHaveBeenCalled(); // a failed chunk is not silent
    warn.mockRestore();
  });
});
