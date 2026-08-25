/**
 * @file_name: createAgentSkills.ts
 * @author:
 * @date: 2026-08-25
 * @description: Keeps backend-provisioned default skills separate from skills explicitly installed during agent creation.
 */
import type { MarketplaceSkillItem } from '@/types/skills';

export function getIncludedSkills(
  defaultSkills: MarketplaceSkillItem[],
  selectedSkills: ReadonlyMap<string, MarketplaceSkillItem>,
): MarketplaceSkillItem[] {
  const included = new Map(defaultSkills.map((skill) => [skill.skill_id, skill]));
  for (const [skillId, skill] of selectedSkills) included.set(skillId, skill);
  return Array.from(included.values());
}

export function getManuallyInstalledSkills(
  selectedSkills: ReadonlyMap<string, MarketplaceSkillItem>,
  defaultSkillIds: ReadonlySet<string>,
): MarketplaceSkillItem[] {
  return Array.from(selectedSkills.values()).filter(
    (skill) => !defaultSkillIds.has(skill.skill_id),
  );
}

export function isAlreadyInstalledSkillError(error: unknown): boolean {
  return typeof error === 'object' && error !== null && 'status' in error && error.status === 409;
}
