/**
 * SetupPage subscription surface (P0: landing must let subscription-only
 * users through).
 *
 * Owner-decided layout (2026-08-28 round 2): the page keeps its original
 * shape — OneKeyOnboard primary, everything else behind the collapsed
 * "Advanced setup" disclosure — but the fold now reveals the subscription
 * connect DIRECTLY (it used to be buried further inside ProviderSettings'
 * add modal → Sign in tab). On CLOUD the subscription block must not
 * render even with the fold open (the backend 403s OAuth card types for
 * non-staff — the UI must not advertise that path, direct /setup URL
 * visits included). Connecting does NOT auto-navigate; the footer flips
 * to "Get Started" via the live re-probe instead.
 */
import { describe, test, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';

const getProvidersMock = vi.fn();
vi.mock('@/lib/api', () => ({
  api: { getProviders: (...a: unknown[]) => getProvidersMock(...a) },
}));
vi.mock('@/lib/productAnalytics', () => ({ captureProductEvent: vi.fn() }));
const navigateMock = vi.fn();
vi.mock('react-router-dom', () => ({ useNavigate: () => navigateMock }));
vi.mock('@/hooks', () => ({ useTheme: () => ({ isDark: false }) }));
vi.mock('@/lib/providersApi', () => ({
  providerApiUrl: (path = '') => `http://test/api/providers${path}`,
  authFetch: vi.fn(),
}));

let runtimeMode: 'local' | 'cloud' = 'local';
vi.mock('@/stores', () => ({
  useRuntimeStore: (sel?: (s: unknown) => unknown) => {
    const s = { mode: runtimeMode };
    return sel ? sel(s) : s;
  },
}));

vi.mock('@/components/settings/OneKeyOnboard', () => ({
  OneKeyOnboard: () => <div data-testid="one-key-card" />,
}));
vi.mock('@/components/settings/ProviderSettings', () => ({
  ProviderSettings: () => <div data-testid="provider-settings" />,
}));
vi.mock('@/components/settings/SubscriptionConnect', () => ({
  SubscriptionConnect: () => <div data-testid="subscription-connect" />,
}));

import { SetupPage } from '@/pages/SetupPage';

beforeEach(() => {
  getProvidersMock.mockReset();
  getProvidersMock.mockResolvedValue({ success: true, data: { providers: {} } });
  navigateMock.mockReset();
});

const expandAdvanced = () =>
  fireEvent.click(
    screen.getByText(/Advanced setup/, { exact: false }),
  );

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
    runtimeMode = 'cloud';
    render(<SetupPage />);
    expandAdvanced();
    expect(screen.getByTestId('provider-settings')).toBeTruthy();
    expect(screen.queryByTestId('subscription-connect')).toBeNull();
  });
});
