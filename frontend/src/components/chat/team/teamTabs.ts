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

const TAB_LABEL_KEYS: Record<TeamTabId, string> = {
  members: 'chat.team.roster.title',
  artifacts: 'chat.team.workspace.tabArtifacts',
  files: 'chat.team.workspace.tabFiles',
};

export interface TeamTabCounts {
  members: number;
  artifacts: number;
  files: number;
}

/** Live counts ride on the switcher entries — shared files in particular
 *  have no other surface advertising their existence. */
export function teamDrawerCategories(
  counts: TeamTabCounts,
): ReadonlyArray<DrawerSwitcherCategory<TeamTabId>> {
  return [
    {
      label: 'Team',
      labelKey: 'chat.team.drawerCategory',
      tabs: [
        { id: 'members', labelKey: TAB_LABEL_KEYS.members, icon: Users2, count: counts.members },
        { id: 'artifacts', labelKey: TAB_LABEL_KEYS.artifacts, icon: ArtifactsGlyph, count: counts.artifacts },
        { id: 'files', labelKey: TAB_LABEL_KEYS.files, icon: FolderOpen, count: counts.files },
      ],
    },
  ];
}


export function teamTabLabelKey(id: TeamTabId): string {
  return TAB_LABEL_KEYS[id] ?? 'chat.team.roster.title';
}
