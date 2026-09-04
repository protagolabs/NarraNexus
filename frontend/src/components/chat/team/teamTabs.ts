/**
 * @file_name: teamTabs.ts
 * @author: NarraNexus
 * @date: 2026-08-19
 * @description: The team room's drawer panels — members, artifacts, shared
 * files, team management — as ids plus their label keys.
 *
 * The team room's right side IS the single-chat right side: same drawer,
 * same pin/width preferences. Only the panel set differs, and this file is
 * that difference. Each panel is opened by its own toggle in the member bar
 * (the drawer header shows the active panel's name as plain text), so a
 * `Record` keyed by TeamTabId is the whole registry — adding a panel that
 * forgets its label is a compile error.
 */

export type TeamTabId = 'members' | 'artifacts' | 'files' | 'manage';

const TAB_LABEL_KEYS: Record<TeamTabId, string> = {
  members: 'chat.team.roster.title',
  artifacts: 'chat.team.workspace.tabArtifacts',
  files: 'chat.team.workspace.tabFiles',
  manage: 'chat.team.manage.title',
};

export function teamTabLabelKey(id: TeamTabId): string {
  return TAB_LABEL_KEYS[id] ?? 'chat.team.roster.title';
}
