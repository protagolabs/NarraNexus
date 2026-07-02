/**
 * @file NetmindAccountPanel.test.tsx
 * @description Renders the four panel states (S1/S2/S3) and confirms S0 (local
 * mode) renders nothing. api + i18n + runtimeStore are mocked — no network.
 */
import { render, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, test, vi } from 'vitest';
import { NetmindAccountPanel } from '../NetmindAccountPanel';

// i18n: return the inline default string (2nd arg) so assertions read real copy.
vi.mock('react-i18next', () => ({
  useTranslation: () => ({ t: (_k: string, d?: string) => d ?? _k }),
}));

let mockMode = 'cloud-web';
vi.mock('@/stores/runtimeStore', () => ({
  useRuntimeStore: (sel: (s: { mode: string }) => unknown) => sel({ mode: mockMode }),
}));

const mockGetSubscription = vi.fn();
vi.mock('@/lib/api', () => ({
  api: { getSubscription: (...a: unknown[]) => mockGetSubscription(...a) },
}));

beforeEach(() => {
  mockMode = 'cloud-web';
  mockGetSubscription.mockReset();
});

test('S0: local mode renders nothing', () => {
  mockMode = 'local';
  const { container } = render(<NetmindAccountPanel />);
  expect(container.firstChild).toBeNull();
});

test('S1: free plan (subscription null)', async () => {
  mockGetSubscription.mockResolvedValue({ success: true, data: { subscription: null } });
  render(<NetmindAccountPanel />);
  expect(await screen.findByText(/Free \(not subscribed\)/)).toBeTruthy();
});

test('S2: pro active (auto_renew on)', async () => {
  mockGetSubscription.mockResolvedValue({
    success: true,
    data: {
      subscription: { status: 'ACTIVE', auto_renew: true, current_period_end: 1790000000 },
    },
  });
  render(<NetmindAccountPanel />);
  expect(await screen.findByText(/Pro · active/)).toBeTruthy();
});

test('S3: pro cancelled but in-period (auto_renew off)', async () => {
  mockGetSubscription.mockResolvedValue({
    success: true,
    data: {
      subscription: { status: 'ACTIVE', auto_renew: false, current_period_end: 1790000000 },
    },
  });
  render(<NetmindAccountPanel />);
  expect(await screen.findByText(/still active this period/)).toBeTruthy();
});

test('error: fetch rejects -> error copy, no crash', async () => {
  mockGetSubscription.mockRejectedValue(new Error('401'));
  render(<NetmindAccountPanel />);
  await waitFor(() =>
    expect(screen.getByText(/Could not load subscription status/)).toBeTruthy(),
  );
});

test('sandbox notice always shown in cloud mode', async () => {
  mockGetSubscription.mockResolvedValue({ success: true, data: { subscription: null } });
  render(<NetmindAccountPanel />);
  expect(await screen.findByText(/sandbox service is free for now/)).toBeTruthy();
});
