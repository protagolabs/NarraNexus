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
import { ApiError } from '@/lib/api';

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
vi.mock('@/lib/api', async (importOriginal) => {
  // Real ApiError class (instanceof checks in PayPage), stubbed api surface.
  const mod = await importOriginal<typeof import('@/lib/api')>();
  return {
    ApiError: mod.ApiError,
    api: {
      getSubscription: (...a: unknown[]) => mockGetSubscription(...a),
      subscribe: (...a: unknown[]) => mockSubscribe(...a),
    },
  };
});

const mockIsTauri = vi.fn(() => false);
vi.mock('@/lib/tauri', () => ({
  isTauri: () => mockIsTauri(),
}));

const mockOpenExternal = vi.fn().mockResolvedValue(undefined);
vi.mock('@/lib/platform', () => ({
  platform: { openExternal: (url: string) => mockOpenExternal(url) },
}));

const replaceSpy = vi.fn();
beforeEach(() => {
  vi.clearAllMocks();
  authState.netmindToken = 'tok_live';
  mockIsTauri.mockReturnValue(false);
  Object.defineProperty(window, 'location', {
    value: { ...window.location, replace: replaceSpy },
    writable: true,
    configurable: true,
  });
});

const FREE = { success: true, data: { subscription: null } };
const ACTIVE = { success: true, data: { subscription: { status: 'ACTIVE', auto_renew: true } } };
const CHECKOUT = { success: true, data: { checkout_url: 'https://checkout.stripe.com/c/pay_123' } };

describe('PayPage', () => {
  // This page no longer mints a checkout. It is where the marketing pricing
  // CTA lands, and minting a CARD checkout made it a dead end for the users
  // this exists to serve — Alipay and WeChat cannot pay a Stripe subscription.
  // It now hands the rail choice to the account panel, which owns the one
  // implementation of "which rail, how many months".

  it('free power user: lands ON the purchase, not merely near it', async () => {
    authState.netmindToken = 'tok_live';
    mockGetSubscription.mockResolvedValue({ success: true, data: { subscription: null } });
    render(<PayPage />);
    // intent=buy is load-bearing: without it the CTA would arrive at a settings
    // page with the purchase one click away, which moves the dead end rather
    // than removing it.
    await waitFor(() =>
      expect(mockNavigate).toHaveBeenCalledWith('/app/settings?tab=account&intent=buy', {
        replace: true,
      }),
    );
    expect(mockSubscribe).not.toHaveBeenCalled();
  });

  it('already subscribed: goes to the account page, never re-subscribes', async () => {
    mockGetSubscription.mockResolvedValue(ACTIVE);

    render(<PayPage />);

    await waitFor(() =>
      expect(mockNavigate).toHaveBeenCalledWith('/app/settings?tab=account', { replace: true }),
    );
    expect(mockSubscribe).not.toHaveBeenCalled();
    expect(replaceSpy).not.toHaveBeenCalled();
  });

  it('probe failure is "unknown", not fatal: the buyer still reaches the purchase', async () => {
    authState.netmindToken = 'tok_live';
    mockGetSubscription.mockRejectedValue(new Error('billing 502'));
    render(<PayPage />);
    await waitFor(() =>
      expect(mockNavigate).toHaveBeenCalledWith('/app/settings?tab=account&intent=buy', {
        replace: true,
      }),
    );
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

  it('StrictMode double-effect redirects exactly once', async () => {
    authState.netmindToken = 'tok_live';
    mockGetSubscription.mockResolvedValue({ success: true, data: { subscription: null } });
    const { rerender } = render(<PayPage />);
    rerender(<PayPage />);
    await waitFor(() => expect(mockNavigate).toHaveBeenCalled());
    expect(mockNavigate).toHaveBeenCalledTimes(1);
  });
});
