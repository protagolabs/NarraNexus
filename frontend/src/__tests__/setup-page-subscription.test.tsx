/**
 * SetupPage subscription surface (P0: landing must let subscription-only
 * users through).
 *
 * Owner-decided layout (2026-08-28 round 2): the page keeps its original
 * shape — OneKeyOnboard primary, everything else behind the collapsed
 * "Advanced setup" disclosure — but the fold now reveals the subscription
 * connect DIRECTLY (it used to be buried further inside ProviderSettings'
 * add modal → Sign in tab). These tests pin SetupPage's own mode gate
 * (mode !== 'cloud-web' — 'cloud-web' is the real AppMode value — hides
 * the whole subscription section, heading included; a not-yet-hydrated
 * null mode fails OPEN to local). The authoritative cloud gate lives
 * inside SubscriptionConnect itself (statuses' allowed === false →
 * renders nothing) and is pinned by that component's own tests — this
 * file mocks the component, so it can only speak for the SetupPage layer.
 *
 * The child mocks are INTERACTIVE on purpose: the P0's signature
 * behavior is the full chain "connect a subscription → the footer flips
 * from Skip-for-now to Get Started, with no navigation" — a static-div
 * mock cannot regress-test that chain.
 */
import { describe, test, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { useEffect } from 'react';

const getProvidersMock = vi.fn();
const addProviderMock = vi.fn();
vi.mock('@/lib/api', () => ({
  api: {
    getProviders: (...a: unknown[]) => getProvidersMock(...a),
    addProvider: (...a: unknown[]) => addProviderMock(...a),
  },
  ApiError: class ApiError extends Error {},
}));
vi.mock('@/lib/productAnalytics', () => ({ captureProductEvent: vi.fn() }));
const navigateMock = vi.fn();
vi.mock('react-router-dom', () => ({ useNavigate: () => navigateMock }));
vi.mock('@/hooks', () => ({ useTheme: () => ({ isDark: false }) }));

vi.mock('@/lib/providersApi', () => ({
  providerErrorMessage: () => 'error',
}));

let runtimeMode: 'local' | 'cloud-web' = 'local';
vi.mock('@/stores', () => ({
  useRuntimeStore: (sel?: (s: unknown) => unknown) => {
    const s = { mode: runtimeMode };
    return sel ? sel(s) : s;
  },
}));

vi.mock('@/components/settings/OneKeyOnboard', () => ({
  OneKeyOnboard: () => <div data-testid="one-key-card" />,
}));
// Interactive mock: exposes a button that drives the page's addProvider,
// exactly like the real card's "Add as Provider" does.
vi.mock('@/components/settings/SubscriptionConnect', () => ({
  SubscriptionConnect: ({
    addProvider,
  }: {
    addProvider: (b: Record<string, unknown>) => Promise<boolean>;
  }) => (
    <button
      data-testid="subscription-connect"
      onClick={() => addProvider({ card_type: 'claude_oauth' })}
    >
      mock-connect
    </button>
  ),
}));
// Interactive mock: mirrors the real contract — refetch on refreshToken
// change, then notify via onProvidersChanged (the fallback path).
vi.mock('@/components/settings/ProviderSettings', () => ({
  ProviderSettings: ({
    refreshToken,
    onProvidersChanged,
  }: {
    refreshToken?: number;
    onProvidersChanged?: () => void;
  }) => {
    useEffect(() => {
      onProvidersChanged?.();
      // The real component refetches whenever the token bumps and then
      // fires the callback; the mock reproduces just that observable.
    }, [refreshToken, onProvidersChanged]);
    return <div data-testid="provider-settings" />;
  },
}));

import { SetupPage } from '@/pages/SetupPage';

beforeEach(() => {
  getProvidersMock.mockReset();
  getProvidersMock.mockResolvedValue({ success: true, data: { providers: {} } });
  addProviderMock.mockReset();
  addProviderMock.mockResolvedValue({ success: true });
  navigateMock.mockReset();
});

const expandAdvanced = () =>
  fireEvent.click(screen.getByText(/Advanced setup/, { exact: false }));

describe('SetupPage subscription surface', () => {
  test('local mode: subscription connect is inside the fold, not the primary area', () => {
    runtimeMode = 'local';
    render(<SetupPage />);
    expect(screen.getByTestId('one-key-card')).toBeTruthy();
    // Collapsed by default — not visible until the fold opens.
    expect(screen.queryByTestId('subscription-connect')).toBeNull();
    expandAdvanced();
    expect(screen.getByTestId('subscription-connect')).toBeTruthy();
    expect(screen.getByTestId('provider-settings')).toBeTruthy();
    // Revealing the fold must not navigate anywhere by itself.
    expect(navigateMock).not.toHaveBeenCalled();
  });

  test('cloud mode: subscription connect never renders, even with the fold open', () => {
    runtimeMode = 'cloud-web';
    render(<SetupPage />);
    expandAdvanced();
    expect(screen.getByTestId('provider-settings')).toBeTruthy();
    expect(screen.queryByTestId('subscription-connect')).toBeNull();
  });

  test('P0 chain: connecting a subscription flips the footer to Get Started without navigating', async () => {
    runtimeMode = 'local';
    // First call (mount probe): no providers → footer shows "Skip for
    // now". Later calls (post-connect re-probe): one subscription card.
    // The non-empty answer MUST be reserved for the later calls, or the
    // footer assertion would pass for the wrong reason.
    getProvidersMock.mockResolvedValueOnce({ success: true, data: { providers: {} } });
    getProvidersMock.mockResolvedValue({
      success: true,
      data: { providers: { p1: { provider_id: 'p1', source: 'claude_oauth', auth_type: 'oauth' } } },
    });

    render(<SetupPage />);
    expect(await screen.findByText('Skip for now')).toBeTruthy();

    expandAdvanced();
    fireEvent.click(screen.getByTestId('subscription-connect'));

    await waitFor(() =>
      expect(addProviderMock).toHaveBeenCalledWith({ card_type: 'claude_oauth' }),
    );
    // Footer flips live — no collapse, no remount, no navigation.
    expect(await screen.findByText('Get Started')).toBeTruthy();
    expect(screen.queryByText('Skip for now')).toBeNull();
    expect(navigateMock).not.toHaveBeenCalled();
  });
});
