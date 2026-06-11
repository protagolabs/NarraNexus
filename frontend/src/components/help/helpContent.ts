/**
 * @file_name: helpContent.ts
 * @author:
 * @date: 2026-06-11
 * @description: Declarative annotation manifests for the help overlay.
 *
 * Each annotation references a `data-help-id` anchor in the live DOM.
 * The overlay measures anchors at open time and silently skips any that
 * are missing or invisible — so manifests never break when the layout
 * evolves; an outdated entry just stops rendering.
 *
 * Density discipline (spec §12.5): ≤ 8 annotations per view. If a view
 * needs more, the view itself has too much UI.
 */

export type AnnotationSide = 'left' | 'right' | 'top' | 'bottom';

export interface HelpAnnotation {
  /** Matches a `data-help-id` attribute in the DOM. */
  helpId: string;
  /** Handwritten note text (English, short, friendly). */
  note: string;
  /** Which side of the anchor the note sits on. */
  side: AnnotationSide;
  /** Lower = drawn earlier in the stagger animation. */
  priority: number;
  /** Also draw a hand-drawn ellipse around the anchor. */
  circle?: boolean;
}

/** Chat view — the main workspace. */
export const CHAT_VIEW_ANNOTATIONS: HelpAnnotation[] = [
  {
    helpId: 'sidebar.create-agent',
    note: 'New agent — start here',
    side: 'right',
    priority: 1,
    circle: true,
  },
  {
    helpId: 'sidebar.agent-list',
    note: 'Your agents, grouped by team. Click a header to fold a group',
    side: 'right',
    priority: 2,
  },
  {
    helpId: 'sidebar.agents-menu',
    note: 'Import, export & team management hide in here',
    side: 'right',
    priority: 3,
  },
  {
    helpId: 'chat.composer',
    note: 'Talk to your agent here',
    side: 'top',
    priority: 4,
  },
  {
    helpId: 'bookmarks.activity',
    note: 'Jobs & inbox live here — it lights up when something happens',
    side: 'left',
    priority: 5,
    circle: true,
  },
  {
    helpId: 'bookmarks.agent',
    note: 'Who your agent is: awareness, skills, memory',
    side: 'left',
    priority: 6,
  },
  {
    helpId: 'chat.cost',
    note: 'What this conversation costs',
    side: 'bottom',
    priority: 7,
  },
];
