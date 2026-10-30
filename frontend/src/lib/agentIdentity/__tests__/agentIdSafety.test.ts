/**
 * The backend logs a signed tenant-boundary violation for an agent id that is
 * not one path segment, and the edge monitor bans an account that sends
 * several different ones. These ids must therefore never leave the browser —
 * a crafted link would otherwise get the victim banned by their own requests.
 */
import { describe, expect, test } from 'vitest';
import { agentIdFromRoute, isSafeAgentId } from '../agentIdSafety';

describe('isSafeAgentId', () => {
  test.each(['agent_1a2b3c4d', 'Legacy Agent', 'a.b', 'agent-é'])('accepts %s', (id) => {
    expect(isSafeAgentId(id)).toBe(true);
  });

  test.each(['', '.', '..', '.hidden', 'a/b', 'a\\b', 'a%2e', 'a\u0000b'])('rejects %j', (id) => {
    expect(isSafeAgentId(id)).toBe(false);
  });
});

describe('agentIdFromRoute', () => {
  test('decodes a normal param', () => {
    expect(agentIdFromRoute('Legacy%20Agent')).toBe('Legacy Agent');
  });

  test('a single-encoded traversal decodes to an unsafe id and is dropped', () => {
    expect(agentIdFromRoute('%2E%2E')).toBe('');
    expect(agentIdFromRoute('a%2Fb')).toBe('');
  });

  test('a double-encoded traversal is dropped, not decoded twice later', () => {
    expect(agentIdFromRoute('%252E%252E')).toBe('');
  });

  test('a malformed escape yields no id instead of throwing', () => {
    expect(agentIdFromRoute('%E0%A4%A')).toBe('');
    expect(agentIdFromRoute(undefined)).toBe('');
  });
});
