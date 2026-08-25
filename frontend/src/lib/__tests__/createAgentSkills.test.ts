/**
 * @file_name: createAgentSkills.test.ts
 * @author:
 * @date: 2026-08-25
 * @description: Tests the default and manually selected skill split used during agent creation.
 */
import { describe, expect, it } from 'vitest';

import {
  getIncludedSkills,
  getManuallyInstalledSkills,
  isAlreadyInstalledSkillError,
} from '@/lib/createAgentSkills';
import type { MarketplaceSkillItem } from '@/types/skills';

const skill = (skillId: string, name: string): MarketplaceSkillItem => ({
  skill_id: skillId,
  version: '1.0.0',
  name,
  capabilities: [],
  tags: [],
  downloads: 0,
  scan_status: 'passed',
  status: 'published',
});

describe('create agent skills', () => {
  const transcribe = skill('netmind-transcribe', 'NetMind Transcribe');
  const vision = skill('netmind-vision', 'NetMind Vision');
  const research = skill('deep-research', 'Deep Research');

  it('includes backend defaults without requiring the user to select them', () => {
    expect(getIncludedSkills([transcribe, vision], new Map())).toEqual([transcribe, vision]);
  });

  it('deduplicates a default skill and preserves additional user selections', () => {
    const selected = new Map([
      [vision.skill_id, vision],
      [research.skill_id, research],
    ]);

    expect(getIncludedSkills([transcribe, vision], selected)).toEqual([
      transcribe,
      vision,
      research,
    ]);
  });

  it('only returns non-default skills for explicit installation', () => {
    const selected = new Map([
      [vision.skill_id, vision],
      [research.skill_id, research],
    ]);

    expect(getManuallyInstalledSkills(selected, new Set([transcribe.skill_id, vision.skill_id])))
      .toEqual([research]);
  });

  it('treats an already-installed response as an idempotent success', () => {
    expect(isAlreadyInstalledSkillError({ status: 409 })).toBe(true);
    expect(isAlreadyInstalledSkillError({ status: 500 })).toBe(false);
    expect(isAlreadyInstalledSkillError(new Error('Conflict'))).toBe(false);
  });
});
