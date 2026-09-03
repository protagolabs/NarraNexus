/**
 * @file_name: settingsSections.ts
 * @author: Bin Liang
 * @date: 2026-09-03
 * @description: Settings sections registry — the left nav of the settings page and the pane per id.
 *
 * `SettingsPage` reads its nav items and renders the active pane from this
 * registry; `desktopOnly` / `cloudHidden` / `neverDefault` keep the existing
 * visibility rules as data so a plugin section obeys the same gates.
 */
import type { ComponentType } from 'react';
import type { LucideIcon } from 'lucide-react';

import { Registry } from './registry';

export interface SettingsSectionProps {
  /** Navigate to another section (used by cross-links such as "manage providers"). */
  navigate: (sectionId: string) => void;
}

export interface SettingsSectionDef {
  labelKey: string;
  icon: LucideIcon;
  component: ComponentType<SettingsSectionProps>;
  order: number;
  desktopOnly?: boolean;
  cloudHidden?: boolean;
  neverDefault?: boolean;
}

export const SETTINGS_SECTIONS = new Registry<SettingsSectionDef>('ui.settingsSections');

export function sortedSettingsSections(opts: { isTauri: boolean; isCloud: boolean }) {
  return SETTINGS_SECTIONS.list()
    .filter((e) => (!e.value.desktopOnly || opts.isTauri) && !(e.value.cloudHidden && opts.isCloud))
    .sort((a, b) => a.value.order - b.value.order);
}
