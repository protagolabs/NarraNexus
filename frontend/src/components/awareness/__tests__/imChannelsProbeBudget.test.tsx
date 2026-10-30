/**
 * @file_name: imChannelsProbeBudget.test.tsx
 * @author: Bin Liang
 * @date: 2026-10-30
 * @description: The Channels section probes each channel's credential once per mount / agent switch / explicit refresh — never in a loop. Guards the 2026-10-30 regression where an unmemoized sorted row array refired the mount effect after every status update (~30 probes/s per open panel, enough to trip the edge flood ban).
 */
import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

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
const CHANNEL_COUNT = CATALOG.length;

const credential = vi.fn(async () => ({ success: true, data: null }));
vi.mock('@/lib/api', () => ({
  api: {
    channelCredential: (...a: unknown[]) => credential(...a),
    pluginChannels: async () => ({ success: true, data: CATALOG }),
  },
}));

const store = vi.hoisted(() => ({ agentId: 'agent_a' }));
vi.mock('@/stores', () => ({ useConfigStore: () => ({ agentId: store.agentId }) }));

/** Let any further effect → fetch → setState rounds run, so a loop would show up in the count. */
const settle = () => act(() => new Promise((r) => setTimeout(r, 200)));

describe('channels status probe budget', () => {
  beforeEach(() => {
    credential.mockReset();
    credential.mockImplementation(async () => ({ success: true, data: null }));
    store.agentId = 'agent_a';
  });

  it('probes each channel exactly once on mount', async () => {
    await builtinChannelsReady;
    render(<IMChannelsSection />);
    await waitFor(() => expect(credential.mock.calls.length).toBeGreaterThanOrEqual(CHANNEL_COUNT));
    await settle();
    expect(credential).toHaveBeenCalledTimes(CHANNEL_COUNT);
  });

  it('a re-render with the same agent does not probe again', async () => {
    await builtinChannelsReady;
    const { rerender } = render(<IMChannelsSection />);
    await waitFor(() => expect(credential.mock.calls.length).toBeGreaterThanOrEqual(CHANNEL_COUNT));
    rerender(<IMChannelsSection />);
    await settle();
    expect(credential).toHaveBeenCalledTimes(CHANNEL_COUNT);
  });

  it('switching agent probes once more, for the new agent', async () => {
    await builtinChannelsReady;
    const { rerender } = render(<IMChannelsSection />);
    await waitFor(() => expect(credential.mock.calls.length).toBeGreaterThanOrEqual(CHANNEL_COUNT));
    store.agentId = 'agent_b';
    rerender(<IMChannelsSection />);
    await settle();
    expect(credential).toHaveBeenCalledTimes(CHANNEL_COUNT * 2);
    expect(credential.mock.calls.slice(CHANNEL_COUNT).every((c) => c[1] === 'agent_b')).toBe(true);
  });

  it('the refresh button probes exactly one more round', async () => {
    await builtinChannelsReady;
    render(<IMChannelsSection />);
    await waitFor(() => expect(credential.mock.calls.length).toBeGreaterThanOrEqual(CHANNEL_COUNT));
    fireEvent.click(screen.getByRole('button', { name: /refresh status/i }));
    await settle();
    expect(credential).toHaveBeenCalledTimes(CHANNEL_COUNT * 2);
  });
  it('collapsing and re-opening the section probes exactly one more round', async () => {
    await builtinChannelsReady;
    render(<IMChannelsSection />);
    await waitFor(() => expect(credential.mock.calls.length).toBeGreaterThanOrEqual(CHANNEL_COUNT));
    const header = screen.getByRole('button', { name: /IM Channels/i });
    fireEvent.click(header); // collapse: no probe
    await settle();
    expect(credential).toHaveBeenCalledTimes(CHANNEL_COUNT);
    fireEvent.click(header); // re-open: one round
    await settle();
    expect(credential).toHaveBeenCalledTimes(CHANNEL_COUNT * 2);
  });

  it("a slow round for the previous agent never overwrites the current agent's badges", async () => {
    await builtinChannelsReady;
    // agent_a: every channel bound + active, but its round is held back;
    // agent_b: nothing bound, answers immediately.
    let releaseA: () => void = () => {};
    const aGate = new Promise<void>((r) => { releaseA = r; });
    credential.mockImplementation(async (...args: unknown[]) => {
      const [channel, agent] = args as [string, string];
      if (agent === 'agent_a') {
        await aGate;
        return { success: true, data: { channel, agent_id: agent, enabled: true } };
      }
      return { success: true, data: null };
    });
    const { rerender } = render(<IMChannelsSection />);
    await waitFor(() => expect(credential.mock.calls.length).toBeGreaterThanOrEqual(CHANNEL_COUNT));
    store.agentId = 'agent_b';
    rerender(<IMChannelsSection />);
    await waitFor(() => expect(screen.getByText(`0/${CHANNEL_COUNT} connected`)).toBeInTheDocument());
    releaseA();
    await settle();
    expect(screen.getByText(`0/${CHANNEL_COUNT} connected`)).toBeInTheDocument();
    expect(screen.queryByText('✓ connected')).toBeNull();
  });
});
