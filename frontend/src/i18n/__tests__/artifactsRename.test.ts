/**
 * @file_name: artifactsRename.test.ts
 * @description: Regression guard for req #3 — the Chinese artifacts term is
 * "可视化产物", never the old "制品". Any reintroduced 制品 turns this red.
 */
import { describe, it, expect } from 'vitest';
import zh from '@/i18n/locales/zh.json';

describe('req #3 artifacts term rename (zh)', () => {
  it('rail.artifacts label uses 可视化产物', () => {
    expect((zh as Record<string, any>).rail.artifacts).toBe('可视化产物');
  });

  it('no 制品 remains anywhere in the zh bundle', () => {
    expect(JSON.stringify(zh).includes('制品')).toBe(false);
  });
});
