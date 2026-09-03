/**
 * @file_name: tabs.ts
 * @author:
 * @date: 2026-06-11
 * @description: The atomic-tab registry for the bookmark strip.
 *
 * Owner-decided IA (2026-06-11): the smallest unit is an atomic tab —
 * ONE tab opens exactly ONE panel, never a stack of sections to scroll
 * through. Categories group the atomic tabs visually on the strip.
 *
 * This file is the single source of truth for: tab ids, labels, icons,
 * category grouping, and how bookmarkStore signals map onto each tab's
 * status (spinner / attention pulse / info dot / badge count).
 */

import { createElement, forwardRef } from 'react';
import {
  Sparkles,
  FolderOpen,
  Radio,
  Home,
  Network,
  ListTodo,
  Inbox,
  Wand2,
  Puzzle,
  Server,
  BookOpen,
  type LucideIcon,
  type LucideProps,
} from 'lucide-react';
import type { AgentBookmarkState } from '@/stores/bookmarkStore';
import { useBookmarkStore } from '@/stores/bookmarkStore';

/**
 * Artifacts glyph — node-circle joined to a frame (Owner-provided reference,
 * 2026-08-06 screenshot). Not in lucide, so drawn locally with the same
 * stroke conventions; typed as LucideIcon so the registry / palette / header
 * can use it interchangeably.
 */
export const ArtifactsGlyph = forwardRef<SVGSVGElement, LucideProps>(
  function ArtifactsGlyph({ size = 24, strokeWidth = 2, ...props }, ref) {
    return createElement(
      'svg',
      {
        ref,
        xmlns: 'http://www.w3.org/2000/svg',
        width: size,
        height: size,
        viewBox: '0 0 24 24',
        fill: 'none',
        stroke: 'currentColor',
        strokeWidth,
        strokeLinecap: 'round',
        strokeLinejoin: 'round',
        ...props,
      },
      createElement('circle', { cx: 6, cy: 17, r: 3, key: 'c' }),
      createElement('path', { d: 'M8.2 14.8 12 11', key: 'l' }),
      createElement('rect', { x: 12, y: 3.5, width: 8.5, height: 8.5, rx: 1.5, key: 'r' }),
    );
  },
) as unknown as LucideIcon;

export type AtomicTabId =
  | 'builder'
  | 'awareness'
  | 'workspace'
  | 'channels'
  | 'smarthome'
  | 'social'
  | 'jobs'
  | 'inbox'
  | 'artifacts'
  | 'skills'
  | 'mcp'
  | 'memory';

export interface AtomicTabDef {
  id: AtomicTabId;
  label: string;
  /** i18n key (namespace `rail`) for the label; `label` is the fallback. */
  labelKey: string;
  icon: LucideIcon;
  /** Short caption for the 64px strip when label is too long. */
  stripLabel?: string;
  /** i18n key for `stripLabel` when present. */
  stripLabelKey?: string;
  /**
   * Offered only in a specific context. `'studio'`: the creation studio is
   * open on the current agent. The tab stays REGISTERED regardless (so
   * `tabLabelKey` / `tabDescKey` resolve for a drawer that is already on it);
   * only the pickable lists — drawer switcher, ⌘K palette — filter on it.
   * One field here rather than a filter in each consumer: a new consumer of
   * `STRIP_CATEGORIES` inherits the rule instead of re-deriving it.
   */
  conditional?: 'studio';
}

/** What the pickable lists need to know to decide what to offer. */
export interface TabVisibilityContext {
  studioOpen: boolean;
}

export interface StripCategory {
  label: string;
  /** i18n key (namespace `rail`) for the category label; `label` is the fallback. */
  labelKey: string;
  tabs: AtomicTabDef[];
  /** When set, the group shows a colored brand header (instead of just the
   *  divider hairline). Used for the Narra/Nexus spine. */
  title?: string;
  /** i18n key for the brand `title` when present. */
  titleKey?: string;
  /** Brand accent for the title: carbon (Narra) or silicon (Nexus). */
  accent?: 'carbon' | 'silicon';
}

/** Strip layout, top to bottom. ONE content per tab — no nested stacks.
 *  Narra·Memory (carbon) and Nexus·Network (silicon) carry colored brand
 *  headers; Memory sits between Inbox and Social so the spine reads
 *  Activity → Narrative → Nexus. */
export const STRIP_CATEGORIES: StripCategory[] = [
  {
    label: 'Config',
    labelKey: 'rail.category.config',
    tabs: [
      // Creation studio. Sits first in Config because it is the "start here"
      // of configuration — a conversation that fills the other tabs in.
      {
        id: 'builder',
        label: 'Builder',
        labelKey: 'rail.builder',
        icon: Wand2,
        conditional: 'studio',
      },
      { id: 'awareness', label: 'Awareness', labelKey: 'rail.awareness', icon: Sparkles },
      { id: 'workspace', label: 'Workspace', labelKey: 'rail.workspace', icon: FolderOpen },
      { id: 'channels', label: 'Channels', labelKey: 'rail.channels', icon: Radio },
      { id: 'smarthome', label: 'Smart Home', labelKey: 'rail.smarthome', icon: Home },
    ],
  },
  {
    label: 'Activity',
    labelKey: 'rail.category.activity',
    tabs: [
      { id: 'jobs', label: 'Jobs', labelKey: 'rail.jobs', icon: ListTodo },
      { id: 'inbox', label: 'Inbox', labelKey: 'rail.inbox', icon: Inbox },
      { id: 'artifacts', label: 'Artifacts', labelKey: 'rail.artifacts', icon: ArtifactsGlyph },
    ],
  },
  {
    label: 'Narra',
    labelKey: 'rail.category.narra',
    title: 'Narra',
    titleKey: 'rail.brand.narra',
    accent: 'carbon',
    tabs: [{ id: 'memory', label: 'Memory', labelKey: 'rail.memory', icon: BookOpen }],
  },
  {
    label: 'Nexus',
    labelKey: 'rail.category.nexus',
    title: 'Nexus',
    titleKey: 'rail.brand.nexus',
    accent: 'silicon',
    tabs: [
      {
        id: 'social',
        label: 'Social Network',
        labelKey: 'rail.social',
        icon: Network,
        stripLabel: 'Network',
        stripLabelKey: 'rail.socialShort',
      },
    ],
  },
  {
    label: 'Skills',
    labelKey: 'rail.category.skills',
    tabs: [
      { id: 'skills', label: 'Skills', labelKey: 'rail.skills', icon: Puzzle },
      {
        id: 'mcp',
        label: 'MCP Servers',
        labelKey: 'rail.mcp',
        icon: Server,
        stripLabel: 'MCP',
        stripLabelKey: 'rail.mcpShort',
      },
    ],
  },
];

export const ALL_TABS: AtomicTabDef[] = STRIP_CATEGORIES.flatMap((c) => c.tabs);

function tabOffered(tab: AtomicTabDef, ctx: TabVisibilityContext): boolean {
  return tab.conditional !== 'studio' || ctx.studioOpen;
}

/**
 * The categories a user may PICK from right now — `STRIP_CATEGORIES` with the
 * conditional tabs removed (and a category dropped once it is empty). The
 * drawer's switcher and the ⌘K palette both go through here, so a conditional
 * tab can never leak out of one entry while being hidden in another.
 */
export function visibleCategories(ctx: TabVisibilityContext): StripCategory[] {
  return STRIP_CATEGORIES.map((c) => ({ ...c, tabs: c.tabs.filter((t) => tabOffered(t, ctx)) })).filter(
    (c) => c.tabs.length > 0,
  );
}

/** Flat form of `visibleCategories`. */
export function visibleTabs(ctx: TabVisibilityContext): AtomicTabDef[] {
  return ALL_TABS.filter((t) => tabOffered(t, ctx));
}

export function tabLabel(id: AtomicTabId): string {
  return ALL_TABS.find((t) => t.id === id)?.label ?? id;
}

/** i18n key (namespace `rail`) for a tab's label, for consumers with a `t`. */
export function tabLabelKey(id: AtomicTabId): string {
  return ALL_TABS.find((t) => t.id === id)?.labelKey ?? `rail.${id}`;
}

/**
 * i18n key for a tab's one-sentence explainer — what the panel is for and
 * how a user works with it. Shown behind the ? icon in the drawer header
 * and as hover text on the detail-menu items, so users who never read the
 * docs still get oriented (Owner 2026-08-06).
 */
export function tabDescKey(id: AtomicTabId): string {
  return `rail.desc.${id}`;
}

// ---------------------------------------------------------------------------
// bookmarkStore signal → tab status mapping
// ---------------------------------------------------------------------------

export interface TabStatus {
  status: 'running' | 'attention' | 'info' | 'none';
  badge?: number;
}

/** Which bookmarkStore highlight keys belong to a tab. */
function keysForTab(state: AgentBookmarkState, id: AtomicTabId): string[] {
  switch (id) {
    case 'jobs':
      return Object.keys(state.highlights).filter((k) => k.startsWith('job:'));
    case 'inbox':
      return state.highlights['inbox'] ? ['inbox'] : [];
    case 'awareness':
      return state.highlights['profile:awareness'] ? ['profile:awareness'] : [];
    default:
      return [];
  }
}

/** Derive the visual status of one atomic tab from the agent's state. */
export function deriveTabStatus(
  state: AgentBookmarkState | undefined,
  id: AtomicTabId,
): TabStatus {
  if (!state) return { status: 'none' };

  if (id === 'jobs') {
    if (state.badges.failedJobs > 0) {
      return { status: 'attention', badge: state.badges.failedJobs };
    }
    if (state.subBookmarks.some((s) => s.key.startsWith('job:') && s.status === 'running')) {
      return { status: 'running' };
    }
    if (keysForTab(state, id).some((k) => state.highlights[k] === 'info')) {
      return { status: 'info' };
    }
    return { status: 'none' };
  }

  if (id === 'inbox') {
    if (state.badges.inboxUnread > 0) {
      return { status: 'attention', badge: state.badges.inboxUnread };
    }
    return { status: 'none' };
  }

  if (id === 'awareness') {
    if (state.highlights['profile:awareness']) return { status: 'info' };
    return { status: 'none' };
  }

  return { status: 'none' };
}

/**
 * User opened a tab — clear its 'info' highlights (attention/badges only
 * clear when the underlying condition is resolved; see bookmarkStore).
 */
export function markTabOpened(agentId: string, id: AtomicTabId): void {
  const store = useBookmarkStore.getState();
  const state = store.agents[agentId];
  if (!state) return;
  for (const key of keysForTab(state, id)) {
    store.markOpened(agentId, key);
  }
}
