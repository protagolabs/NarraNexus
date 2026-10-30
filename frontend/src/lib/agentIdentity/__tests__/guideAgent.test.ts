/**
 * The agent list carries every public agent on the platform. A stranger's
 * agent that is itself still bootstrap-active must never be taken for the
 * viewer's guide: the welcome flow would introduce it and select it, and every
 * panel would then read an agent the viewer cannot access.
 */
import { describe, expect, test } from 'vitest';
import type { AgentInfo } from '@/types';
import { pickGuideAgent } from '../guideAgent';

const a = (agent_id: string, created_by: string, bootstrap_active = false) =>
  ({ agent_id, created_by, bootstrap_active }) as AgentInfo;

describe('pickGuideAgent', () => {
  test("picks the viewer's own bootstrap-active agent, skipping a stranger's", () => {
    expect(pickGuideAgent([a('public', 'stranger', true), a('guide', 'me', true)], 'me')?.agent_id).toBe('guide');
  });

  test("a stranger's bootstrap-active agent alone is no guide", () => {
    expect(pickGuideAgent([a('public', 'stranger', true)], 'me')).toBeNull();
  });

  test('no bootstrap-active own agent, or no viewer, means no guide', () => {
    expect(pickGuideAgent([a('mine', 'me')], 'me')).toBeNull();
    expect(pickGuideAgent([a('guide', '', true)], '')).toBeNull();
  });
});
