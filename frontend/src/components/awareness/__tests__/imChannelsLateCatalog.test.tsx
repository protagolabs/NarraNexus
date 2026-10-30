/**
 * @file_name: imChannelsLateCatalog.test.tsx
 * @author: Bin Liang
 * @date: 2026-10-30
 * @description: The Channels section can mount before the backend channel catalog arrives (the prod order); the rows landing later must cost exactly one probe round, not start a loop.
 */
import { act, render, waitFor } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';

import { IMChannelsSection } from '../IMChannelsSection';
import { builtinChannelsReady } from '../registerBuiltinChannels';

const CATALOG = vi.hoisted(() =>
  ['lark', 'slack', 'telegram', 'wechat', 'narramessenger', 'discord'].map((name, i) => ({
    name,
    display_name: name,
    owner: `builtin.channels.${name}`,
    ui: { label: name, icon: 'bot', order: (i + 1) * 10 },
  })),
);

// The catalog request stays pending until the test releases it.
const gate = vi.hoisted(() => {
  let release: () => void = () => {};
  const ready = new Promise<void>((r) => { release = r; });
  return { ready, release: () => release() };
});

const credential = vi.fn(async () => ({ success: true, data: null }));
vi.mock('@/lib/api', () => ({
  api: {
    channelCredential: (...a: unknown[]) => credential(...a),
    pluginChannels: async () => {
      await gate.ready;
      return { success: true, data: CATALOG };
    },
  },
}));
vi.mock('@/stores', () => ({ useConfigStore: () => ({ agentId: 'agent_a' }) }));

describe('channels section mounted before the catalog', () => {
  it('probes one round once the rows arrive, then stays quiet', async () => {
    render(<IMChannelsSection />);
    await act(() => new Promise((r) => setTimeout(r, 50)));
    expect(credential).not.toHaveBeenCalled();

    gate.release();
    await builtinChannelsReady;
    await waitFor(() => expect(credential.mock.calls.length).toBeGreaterThanOrEqual(CATALOG.length));
    await act(() => new Promise((r) => setTimeout(r, 200)));
    expect(credential).toHaveBeenCalledTimes(CATALOG.length);
  });
});
