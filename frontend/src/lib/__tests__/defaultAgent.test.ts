/**
 * The agent list includes every public agent on the platform, newest first.
 * Defaulting to `agents[0]` selected a stranger's agent, so every panel read
 * an agent the viewer cannot access (a stream of 403s the edge bans on).
 */
import { describe, expect, test } from 'vitest';
import type { AgentInfo } from '@/types/api';
import { pickDefaultAgent } from '../defaultAgent';

const a = (agent_id: string, created_by: string) => ({ agent_id, created_by }) as AgentInfo;

describe('pickDefaultAgent', () => {
  test("skips a newer public agent of someone else and picks the viewer's own", () => {
    expect(pickDefaultAgent([a('public_new', 'stranger'), a('mine', 'me'), a('mine_old', 'me')], 'me')).toBe('mine');
  });

  test('no own agent means no selection', () => {
    expect(pickDefaultAgent([a('public_1', 'stranger')], 'me')).toBe('');
    expect(pickDefaultAgent([], 'me')).toBe('');
  });

  test('no viewer means no selection', () => {
    expect(pickDefaultAgent([a('x', '')], '')).toBe('');
  });
});
