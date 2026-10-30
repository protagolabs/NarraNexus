/**
 * @file_name: LoginPage.defaultAgent.test.tsx
 * @description: After login the default selection is the viewer's own agent, never a stranger's public one.
 *
 * `/api/auth/agents` lists every public agent on the platform, newest first,
 * so `agents[0]` used to select whatever public agent someone else created
 * last; every panel then read an agent the viewer cannot access (403s, which
 * the edge monitor bans an IP on).
 */
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { expect, test, vi } from 'vitest';

const { store, api } = vi.hoisted(() => ({
  store: { login: vi.fn(), setNetmindToken: vi.fn(), setAgents: vi.fn(), setAgentId: vi.fn() },
  api: {
    login: vi.fn(),
    getAgents: vi.fn(),
  },
}));

vi.mock('@/stores', () => ({
  useConfigStore: (sel?: (s: unknown) => unknown) => (sel ? sel(store) : store),
  useRuntimeStore: (sel: (s: unknown) => unknown) =>
    sel({ mode: 'local', setMode: vi.fn(), setCloudApiUrl: vi.fn() }),
}));
vi.mock('@/hooks', () => ({ useTheme: () => ({ isDark: false }) }));
vi.mock('@/lib/runtimeConfig', () => ({
  getNetmindConfig: () => ({ authApi: 'https://nm.test', accountsUrl: 'https://acc.test', sysCode: 'x', registerUrl: 'https://reg.test' }),
  isPowerLoginAvailable: () => false,
}));
vi.mock('@/lib/api', async (orig) => ({ ...(await orig<typeof import('@/lib/api')>()), api }));

import { LoginPage } from '../LoginPage';

test("local login selects the viewer's own agent, skipping a newer public agent of someone else", async () => {
  api.login.mockResolvedValue({ success: true, token: 't' });
  api.getAgents.mockResolvedValue({
    success: true,
    count: 2,
    agents: [
      { agent_id: 'public_new', created_by: 'stranger' },
      { agent_id: 'mine', created_by: 'alice' },
    ],
  });
  render(<MemoryRouter><LoginPage /></MemoryRouter>);
  fireEvent.change(screen.getByPlaceholderText('your_username'), { target: { value: 'alice' } });
  fireEvent.keyDown(screen.getByPlaceholderText('your_username'), { key: 'Enter' });
  await waitFor(() => expect(store.setAgentId).toHaveBeenCalled());
  expect(store.setAgentId).toHaveBeenCalledWith('mine');
  expect(store.setAgentId).not.toHaveBeenCalledWith('public_new');
});
