/**
 * @file_name: index.ts
 * @date: 2026-06-10
 * @description: Barrel export for the bookmarks component family.
 *   Exports BookmarkStrip (right-edge 36px strip) and BookmarkDrawer
 *   (slide-over / pinned shell) together with their public types.
 */

export { BookmarkStrip } from './BookmarkStrip';
export type { BookmarkTab, BookmarkOpenTarget } from './BookmarkStrip';

export { BookmarkDrawer } from './BookmarkDrawer';
