/**
 * ProviderSettings external refresh signal (refreshToken prop).
 *
 * SetupPage's subscription card lives OUTSIDE ProviderSettings, so a card
 * added through it never went through refreshConfig — the "Your providers"
 * grid kept showing the stale (empty) list until a remount. The
 * refreshToken prop is the signal: bumping it refetches the provider list.
 */
import { describe, test, expect, vi, beforeEach } from 'vitest';
import { render, waitFor } from '@testing-library/react';

const authFetchMock = vi.fn();
vi.mock('@/lib/providersApi', () => ({
  providerApiUrl: (path = '') => `http://test/api/providers${path}`,
  authFetch: (...a: unknown[]) => authFetchMock(...a),
}));

const cfg = { userId: 'u1' };
vi.mock('@/stores', () => ({
  useConfigStore: (sel?: (s: unknown) => unknown) => (sel ? sel(cfg) : cfg),
}));
vi.mock('@/stores/runtimeStore', () => ({
  getApiBaseUrl: () => 'http://test',
}));
vi.mock('@/components/settings/OneKeyOnboard', () => ({
  OneKeyOnboard: () => null,
}));
vi.mock('@/components/settings/SubscriptionConnect', () => ({
  SubscriptionConnect: () => null,
}));

import { ProviderSettings } from '@/components/settings/ProviderSettings';

beforeEach(() => {
  authFetchMock.mockReset();
  authFetchMock.mockResolvedValue({
    json: async () => ({ success: true, data: { providers: {} } }),
  });
});

const providerListCalls = () =>
  authFetchMock.mock.calls.filter(
    ([url]) => String(url) === 'http://test/api/providers',
  ).length;

describe('ProviderSettings refreshToken', () => {
  test('bumping refreshToken refetches the provider list', async () => {
    const { rerender } = render(<ProviderSettings refreshToken={0} />);
    await waitFor(() => expect(providerListCalls()).toBe(1));

    rerender(<ProviderSettings refreshToken={1} />);
    await waitFor(() => expect(providerListCalls()).toBe(2));
  });

  test('re-render without a token change does not refetch', async () => {
    const { rerender } = render(<ProviderSettings refreshToken={0} />);
    await waitFor(() => expect(providerListCalls()).toBe(1));

    rerender(<ProviderSettings refreshToken={0} />);
    // Give any accidental effect a tick to fire.
    await new Promise((r) => setTimeout(r, 20));
    expect(providerListCalls()).toBe(1);
  });
});
