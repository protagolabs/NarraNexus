/**
 * @file_name: teamTabs.ts
 * @author: NarraNexus
 * @date: 2026-08-19
 * @description: The team room's drawer panels — members, artifacts, shared
 * files — as a switcher registry for the shared BookmarkDrawer.
 *
 * The team room's right side IS the single-chat right side: same drawer,
 * same pin/width preferences, same title-dropdown switching. Only the
 * panel set differs, and this registry is that difference.
 */

import { Users2, FolderOpen } from 'lucide-react';
import { ArtifactsGlyph } from '@/components/bookmarks/tabs';
import type { DrawerSwitcherCategory } from '@/components/bookmarks/BookmarkDrawer';

export type TeamTabId = 'members' | 'artifacts' | 'files';

export const TEAM_DRAWER_CATEGORIES: ReadonlyArray<DrawerSwitcherCategory<TeamTabId>> = [
  {
    label: 'Team',
    labelKey: 'chat.team.drawerCategory',
    tabs: [
      { id: 'members', labelKey: 'chat.team.roster.title', icon: Users2 },
      { id: 'artifacts', labelKey: 'chat.team.workspace.tabArtifacts', icon: ArtifactsGlyph },
      { id: 'files', labelKey: 'chat.team.workspace.tabFiles', icon: FolderOpen },
    ],
  },
];

export function teamTabLabelKey(id: TeamTabId): string {
  const tab = TEAM_DRAWER_CATEGORIES[0].tabs.find((t) => t.id === id);
  return tab?.labelKey ?? 'chat.team.roster.title';
}
