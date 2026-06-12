import { afterEach, describe, expect, test, vi } from 'vitest';
import { takeInboundToken } from '../tokenInbound';

afterEach(() => vi.restoreAllMocks());

describe('takeInboundToken', () => {
  test('extracts token + source and strips token from URL', () => {
    const replace = vi.spyOn(window.history, 'replaceState').mockImplementation(() => {});
    const r = takeInboundToken({ search: '?token=abc&source=arena&x=1', pathname: '/app', hash: '' });
    expect(r).toEqual({ handled: true, token: 'abc', source: 'arena' });
    const newUrl = replace.mock.calls[0][2] as string;
    expect(newUrl).not.toContain('token=');
    expect(newUrl).toContain('source=arena');
    expect(newUrl).toContain('x=1');
  });

  test('no token → handled false, source still parsed', () => {
    const r = takeInboundToken({ search: '?source=arena', pathname: '/app', hash: '' });
    expect(r).toEqual({ handled: false, source: 'arena' });
  });
});
