/**
 * The active agent id is what every panel request is built from. An unsafe
 * value (dot-led, separators — see lib/agentIdSafety.ts) must not get into the
 * store from a caller or back out of localStorage, or the browser keeps
 * sending requests the backend logs as tenant-boundary violations.
 */
import { beforeEach, describe, expect, test } from 'vitest';
import { useConfigStore } from '../configStore';

beforeEach(() => {
  useConfigStore.getState().logout();
  useConfigStore.getState().login('alice');
});

describe('setAgentId', () => {
  test('stores a normal id and clears with an empty one', () => {
    useConfigStore.getState().setAgentId('agent_1');
    expect(useConfigStore.getState().agentId).toBe('agent_1');
    useConfigStore.getState().setAgentId('');
    expect(useConfigStore.getState().agentId).toBe('');
  });

  test('ignores an unsafe id and keeps the current selection', () => {
    useConfigStore.getState().setAgentId('agent_1');
    useConfigStore.getState().setAgentId('..');
    useConfigStore.getState().setAgentId('a/b');
    expect(useConfigStore.getState().agentId).toBe('agent_1');
  });
});

describe('rehydrate', () => {
  test('drops an unsafe persisted agent id', async () => {
    localStorage.setItem(
      'narra-nexus-config',
      JSON.stringify({ state: { userId: 'alice', agentId: '.x', isLoggedIn: true }, version: 0 }),
    );
    await useConfigStore.persist.rehydrate();
    expect(useConfigStore.getState().agentId).toBe('');
    expect(useConfigStore.getState().userId).toBe('alice');
  });

  test('keeps a safe persisted agent id', async () => {
    localStorage.setItem(
      'narra-nexus-config',
      JSON.stringify({ state: { userId: 'alice', agentId: 'agent_7', isLoggedIn: true }, version: 0 }),
    );
    await useConfigStore.persist.rehydrate();
    expect(useConfigStore.getState().agentId).toBe('agent_7');
  });
});
