/**
 * SubscriptionConnect — the Claude Code / Codex CLI subscription connect
 * cards, extracted from ProviderSettings' add-modal "Sign in" tab so the
 * landing page (SetupPage) can offer subscription as a first-class path.
 *
 * Pins the connect flows the P0 depends on: Add-as-Provider (host CLI
 * already logged in), setup-token paste, codex add — each must call the
 * parent-supplied addProvider with the right body and fire onConnected on
 * success (SetupPage navigates into the app on that signal).
 */
import { describe, test, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';

vi.mock('@/lib/tauri', () => ({
  isTauri: () => false,
  triggerClaudeLogin: vi.fn(),
  triggerClaudeLogout: vi.fn(),
  cancelClaudeLogin: vi.fn(),
}));

const statusFetchMock = vi.fn();
vi.mock('@/lib/providersApi', () => ({
  providerApiUrl: (path = '') => `http://test/api/providers${path}`,
  authFetch: (...a: unknown[]) => statusFetchMock(...a),
}));

import { SubscriptionConnect } from '@/components/settings/SubscriptionConnect';

const loggedInStatus = {
  cli_installed: true,
  logged_in: true,
  email: 'user@example.com',
  expires_at: null,
};

beforeEach(() => {
  statusFetchMock.mockReset();
  statusFetchMock.mockImplementation((url: string) => {
    const data = String(url).includes('claude-status')
      ? loggedInStatus
      : loggedInStatus;
    return Promise.resolve({ json: async () => ({ success: true, data }) });
  });
});

describe('SubscriptionConnect', () => {
  test('Add as Provider posts claude_oauth and fires onConnected', async () => {
    const addProvider = vi.fn().mockResolvedValue(true);
    const onConnected = vi.fn();
    render(
      <SubscriptionConnect
        claudeCard={null}
        hasCodex={false}
        addProvider={addProvider}
        onConnected={onConnected}
      />,
    );
    const buttons = await screen.findAllByText('Add as Provider');
    fireEvent.click(buttons[0]);
    await waitFor(() =>
      expect(addProvider).toHaveBeenCalledWith({ card_type: 'claude_oauth' }),
    );
    await waitFor(() => expect(onConnected).toHaveBeenCalled());
  });

  test('setup-token paste posts the token and fires onConnected', async () => {
    const addProvider = vi.fn().mockResolvedValue(true);
    const onConnected = vi.fn();
    render(
      <SubscriptionConnect
        claudeCard={null}
        hasCodex={false}
        addProvider={addProvider}
        onConnected={onConnected}
      />,
    );
    const input = await screen.findByPlaceholderText(/setup-token|sk-ant-oat/i);
    fireEvent.change(input, { target: { value: 'sk-ant-oat01-xyz' } });
    fireEvent.click(screen.getByText('Connect with token'));
    await waitFor(() =>
      expect(addProvider).toHaveBeenCalledWith({
        card_type: 'claude_oauth',
        api_key: 'sk-ant-oat01-xyz',
      }),
    );
    await waitFor(() => expect(onConnected).toHaveBeenCalled());
  });

  test('failed addProvider does not fire onConnected', async () => {
    const addProvider = vi.fn().mockResolvedValue(false);
    const onConnected = vi.fn();
    render(
      <SubscriptionConnect
        claudeCard={null}
        hasCodex={false}
        addProvider={addProvider}
        onConnected={onConnected}
      />,
    );
    const buttons = await screen.findAllByText('Add as Provider');
    fireEvent.click(buttons[0]);
    await waitFor(() => expect(addProvider).toHaveBeenCalled());
    expect(onConnected).not.toHaveBeenCalled();
  });
});
