/**
 * @file_name: PayPage.test.tsx
 * @date: 2026-07-31
 * @description: /pay bounce-route branches — the P0 checkout-funnel fix.
 * The page's contract: a free Power user is redirected to Stripe with zero
 * interaction; every other state degrades to the account page or an
 * explicit retryable error, never a dead end.
 */

import { beforeEach, describe, expect, it, vi } from 'vitest';
import { render, screen, waitFor, fireEvent } from '@testing-library/react';
import { StrictMode } from 'react';
import { PayPage } from '../PayPage';

const mockNavigate = vi.fn();
vi.mock('react-router-dom', () => ({
  useNavigate: () => mockNavigate,
}));

const authState = { netmindToken: 'tok_live' };
vi.mock('@/stores', () => ({
  useConfigStore: (selector: (s: typeof authState) => unknown) => selector(authState),
}));

const mockGetSubscription = vi.fn();
const mockSubscribe = vi.fn();
vi.mock('@/lib/api', () => ({
  api: {
    getSubscription: (...a: unknown[]) => mockGetSubscription(...a),
    subscribe: (...a: unknown[]) => mockSubscribe(...a),
  },
}));

const assignSpy = vi.fn();
beforeEach(() => {
  vi.clearAllMocks();
  authState.netmindToken = 'tok_live';
  Object.defineProperty(window, 'location', {
    value: { ...window.location, assign: assignSpy },
    writable: true,
    configurable: true,
  });
});

const FREE = { success: true, data: { subscription: null } };
const ACTIVE = { success: true, data: { subscription: { status: 'ACTIVE', auto_renew: true } } };

describe('PayPage', () => {
  it('free power user: mints a checkout session and redirects same-tab', async () => {
    mockGetSubscription.mockResolvedValue(FREE);
    mockSubscribe.mockResolvedValue({ success: true, data: { checkout_url: 'https://checkout.stripe.com/c/pay_123' } });

    render(<PayPage />);

    await waitFor(() =>
      expect(assignSpy).toHaveBeenCalledWith('https://checkout.stripe.com/c/pay_123'),
    );
    expect(mockNavigate).not.toHaveBeenCalled();
  });

  it('already subscribed: goes to the account page, never re-subscribes', async () => {
    mockGetSubscription.mockResolvedValue(ACTIVE);

    render(<PayPage />);

    await waitFor(() =>
      expect(mockNavigate).toHaveBeenCalledWith('/app/settings?tab=account', { replace: true }),
    );
    expect(mockSubscribe).not.toHaveBeenCalled();
    expect(assignSpy).not.toHaveBeenCalled();
  });

  it('non-Power session: goes to the account page without touching billing APIs', async () => {
    authState.netmindToken = '';

    render(<PayPage />);

    await waitFor(() =>
      expect(mockNavigate).toHaveBeenCalledWith('/app/settings?tab=account', { replace: true }),
    );
    expect(mockGetSubscription).not.toHaveBeenCalled();
    expect(mockSubscribe).not.toHaveBeenCalled();
  });

  it('subscribe failure: shows a retryable error, and retry re-runs the flow', async () => {
    mockGetSubscription.mockResolvedValue(FREE);
    mockSubscribe.mockRejectedValueOnce(new Error('upstream 502'));

    render(<PayPage />);

    expect(await screen.findByText("Couldn't start checkout")).toBeInTheDocument();
    expect(screen.getByText('upstream 502')).toBeInTheDocument();

    mockSubscribe.mockResolvedValueOnce({ success: true, data: { checkout_url: 'https://checkout.stripe.com/c/retry' } });
    fireEvent.click(screen.getByRole('button', { name: 'Try again' }));

    await waitFor(() =>
      expect(assignSpy).toHaveBeenCalledWith('https://checkout.stripe.com/c/retry'),
    );
  });

  it('missing checkout_url in a success envelope is an error, not a silent stop', async () => {
    mockGetSubscription.mockResolvedValue(FREE);
    mockSubscribe.mockResolvedValue({ success: true, data: {} });

    render(<PayPage />);

    expect(await screen.findByText("Couldn't start checkout")).toBeInTheDocument();
    expect(assignSpy).not.toHaveBeenCalled();
  });

  it('StrictMode double-effect mints exactly one checkout session', async () => {
    mockGetSubscription.mockResolvedValue(FREE);
    mockSubscribe.mockResolvedValue({ success: true, data: { checkout_url: 'https://checkout.stripe.com/c/once' } });

    render(
      <StrictMode>
        <PayPage />
      </StrictMode>,
    );

    await waitFor(() => expect(assignSpy).toHaveBeenCalled());
    expect(mockSubscribe).toHaveBeenCalledTimes(1);
  });
});
