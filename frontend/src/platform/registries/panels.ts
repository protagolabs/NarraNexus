/**
 * @file_name: panels.ts
 * @author: Bin Liang
 * @date: 2026-09-03
 * @description: Drawer panel registry — the right-hand rail tabs and the component each renders.
 *
 * `bookmarks/tabs.ts` keeps the strip layout (categories, icons, labels);
 * this registry maps a tab id to its lazy panel component so
 * `BookmarkPanelHost` dispatches by lookup instead of an `&&` chain, and a
 * plugin can add a panel (and a strip entry) without touching the host.
 */
import type { ComponentType, LazyExoticComponent } from 'react';

import { Registry } from './registry';

export interface PanelProps {
  agentId: string;
}

export interface PanelDef {
  component: LazyExoticComponent<ComponentType<PanelProps>> | ComponentType<PanelProps>;
}

export const PANELS = new Registry<PanelDef>('ui.panels');
