/**
 * @file_name: labelRenames.test.ts
 * @description: Regression guard for the req #2 UI label renames —
 * inner-thoughts → activity log, and the "you workspace" nav/page → my world.
 * Reverting any rename in en/zh turns one of these red.
 */
import { describe, it, expect } from 'vitest';
import en from '@/i18n/locales/en.json';
import zh from '@/i18n/locales/zh.json';

describe('req #2 label renames', () => {
  it('inner-thoughts tab is renamed to activity log', () => {
    expect((en as Record<string, any>).chat.innerThoughts).toBe('Activity Log');
    expect((zh as Record<string, any>).chat.innerThoughts).toBe('后台运行记录');
  });

  it('the you-workspace nav row + page title become "My World" / 我的世界', () => {
    expect((en as Record<string, any>).sidebar.workspace).toBe('My World');
    expect((zh as Record<string, any>).sidebar.workspace).toBe('我的世界');
    expect((en as Record<string, any>).pages.you.you).toBe('My World');
    expect((zh as Record<string, any>).pages.you.you).toBe('我的世界');
  });
});
