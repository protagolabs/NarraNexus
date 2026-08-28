/**
 * SubscriptionConnect — the Claude Code / Codex CLI subscription connect
 * cards, extracted from ProviderSettings' add-modal "Sign in" tab so the
 * landing page (SetupPage) can reach subscription sign-in without the
 * modal detour.
 *
 * Pins:
 * - the three connect flows (claude add-as-provider, setup-token paste,
 *   codex add-as-provider) each post the right body via the
 *   parent-supplied addProvider. Connecting deliberately does NOT
 *   navigate anywhere — the Owner rejected auto-navigation; parents
 *   refresh their own state off addProvider's side effects.
 * - the cloud gate: statuses answering `allowed: false` (cloud
 *   non-staff) render NOTHING — the UI must not advertise a path the
 *   backend 403s. `allowed` is undefined on local / cloud-staff, so the
 *   component must check `=== false`, never truthiness.
 */
import { describe, test, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor, within } from '@testing-library/react';

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

const loggedIn = (email: string, extra: Record<string, unknown> = {}) => ({
  cli_installed: true,
  logged_in: true,
  email,
  expires_at: null,
  ...extra,
});

/** Distinct payloads per status route, so claude/codex assertions can't
 * pass off each other's state. */
function mockStatuses(
  claude: Record<string, unknown>,
  codex: Record<string, unknown>,
) {
  statusFetchMock.mockImplementation((url: string) =>
    Promise.resolve({
      json: async () => ({
        success: true,
        data: String(url).includes('claude-status') ? claude : codex,
      }),
    }),
  );
}

beforeEach(() => {
  statusFetchMock.mockReset();
  mockStatuses(loggedIn('claude@example.com'), loggedIn('codex@example.com'));
});

describe('SubscriptionConnect', () => {
  test('claude Add as Provider posts claude_oauth', async () => {
    const addProvider = vi.fn().mockResolvedValue(true);
    render(
      <SubscriptionConnect claudeCard={null} hasCodex={false} addProvider={addProvider} />,
    );
    const claudeCard = await screen.findByTestId('claude-connect-card');
    fireEvent.click(within(claudeCard).getByText('Add as Provider'));
    await waitFor(() =>
      expect(addProvider).toHaveBeenCalledWith({ card_type: 'claude_oauth' }),
    );
  });

  test('codex Add as Provider posts codex_oauth', async () => {
    const addProvider = vi.fn().mockResolvedValue(true);
    render(
      <SubscriptionConnect claudeCard={null} hasCodex={false} addProvider={addProvider} />,
    );
    const codexCard = await screen.findByTestId('codex-connect-card');
    fireEvent.click(within(codexCard).getByText('Add as Provider'));
    await waitFor(() =>
      expect(addProvider).toHaveBeenCalledWith({ card_type: 'codex_oauth' }),
    );
  });

  test('setup-token paste posts the token and clears the input on success', async () => {
    const addProvider = vi.fn().mockResolvedValue(true);
    render(
      <SubscriptionConnect claudeCard={null} hasCodex={false} addProvider={addProvider} />,
    );
    const input = await screen.findByPlaceholderText(/sk-ant-oat/i);
    fireEvent.change(input, { target: { value: 'sk-ant-oat01-xyz' } });
    fireEvent.click(screen.getByText('Connect with token'));
    await waitFor(() =>
      expect(addProvider).toHaveBeenCalledWith({
        card_type: 'claude_oauth',
        api_key: 'sk-ant-oat01-xyz',
      }),
    );
    await waitFor(() => expect((input as HTMLInputElement).value).toBe(''));
  });

  test('failed setup-token keeps the input (so the user can retry)', async () => {
    const addProvider = vi.fn().mockResolvedValue(false);
    render(
      <SubscriptionConnect claudeCard={null} hasCodex={false} addProvider={addProvider} />,
    );
    const input = await screen.findByPlaceholderText(/sk-ant-oat/i);
    fireEvent.change(input, { target: { value: 'sk-ant-oat01-xyz' } });
    fireEvent.click(screen.getByText('Connect with token'));
    await waitFor(() => expect(addProvider).toHaveBeenCalled());
    expect((input as HTMLInputElement).value).toBe('sk-ant-oat01-xyz');
  });

  test('cloud non-staff (allowed: false) renders nothing at all', async () => {
    mockStatuses(
      loggedIn('claude@example.com', { allowed: false }),
      loggedIn('codex@example.com', { allowed: false }),
    );
    const addProvider = vi.fn();
    const { container } = render(
      <SubscriptionConnect claudeCard={null} hasCodex={false} addProvider={addProvider} />,
    );
    await waitFor(() => expect(statusFetchMock).toHaveBeenCalled());
    await waitFor(() => expect(container.firstChild).toBeNull());
    expect(screen.queryByTestId('claude-connect-card')).toBeNull();
    expect(screen.queryByTestId('codex-connect-card')).toBeNull();
  });

  test('local mode (allowed undefined) renders both cards', async () => {
    const addProvider = vi.fn();
    render(
      <SubscriptionConnect claudeCard={null} hasCodex={false} addProvider={addProvider} />,
    );
    expect(await screen.findByTestId('claude-connect-card')).toBeTruthy();
    expect(await screen.findByTestId('codex-connect-card')).toBeTruthy();
  });
});
