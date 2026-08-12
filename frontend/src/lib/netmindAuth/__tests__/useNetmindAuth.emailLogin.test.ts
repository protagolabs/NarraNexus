/**
 * @file_name: useNetmindAuth.emailLogin.test.ts
 * @description: Login failures must show a generic, non-enumerating message.
 *
 * NetMind's /user/emailLogin returns DIFFERENT messages for "user not found"
 * vs "wrong password", which lets an attacker probe which emails are
 * registered. The user-facing error is masked to one generic message; the real
 * upstream message still reaches the auth funnel for internal diagnosis.
 */
import { act, renderHook } from '@testing-library/react';
import { beforeEach, expect, test, vi } from 'vitest';

const netmindPost = vi.fn();
vi.mock('../request', () => ({ netmindPost: (...a: unknown[]) => netmindPost(...a) }));

const reportAuthFunnel = vi.fn();
vi.mock('@/lib/api', () => ({
  api: { reportAuthFunnel: (...a: unknown[]) => reportAuthFunnel(...a) },
}));

vi.mock('../crypto', () => ({
  encryptPassword: () => 'enc',
  generateRandomString: () => 'rand',
}));

import { useNetmindAuth } from '../useNetmindAuth';

beforeEach(() => {
  netmindPost.mockReset();
  reportAuthFunnel.mockReset();
});

test('a rejected login shows a generic message, not the enumerating upstream text', async () => {
  netmindPost.mockRejectedValue(new Error('User not found'));
  const { result } = renderHook(() => useNetmindAuth({ onSuccess: vi.fn() }));

  await act(async () => {
    await result.current.emailLogin('probe@example.com', 'whatever');
  });

  // The upstream "User not found" must NOT reach the user (no account enumeration).
  expect(result.current.error).toBeTruthy();
  expect(result.current.error).not.toMatch(/user not found/i);
  // But the real message still goes to the funnel for internal diagnosis.
  expect(reportAuthFunnel).toHaveBeenCalledWith(
    'netmind_email_login_failed',
    'probe@example.com',
    'User not found',
  );
});
