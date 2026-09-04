/**
 * @file_name: themes.test.ts
 * @author: Bin Liang
 * @date: 2026-09-03
 * @description: Themes may only override declared tokens; apply/clear touch exactly those variables.
 */
import { describe, expect, it } from 'vitest';

import { THEMES, applyTheme, clearTheme, validateThemeTokens } from '@/platform/registries';
import { THEME_TOKENS } from '@/platform/themeTokens.generated';

describe('theme registry', () => {
  it('rejects unknown tokens and unsafe values', () => {
    expect(validateThemeTokens({ '--nm-ink': '#111' })).toEqual([]);
    expect(validateThemeTokens({ '--evil': 'x' })[0]).toMatch(/unknown token/);
    expect(validateThemeTokens({ '--nm-ink': 'url(https://x)' })[0]).toMatch(/unsafe/);
    expect(() => THEMES.register('acme.bad', { displayName: 'Bad', tokens: { '--nope': '1' } })).toThrow(/unknown token/);
    expect(THEME_TOKENS).toContain('--nm-ink');
  });

  it('applies and clears exactly the theme tokens', () => {
    const root = document.createElement('div');
    THEMES.register('acme.dark', { displayName: 'Acme', tokens: { '--nm-ink': '#fff', '--nm-paper': '#000' }, dark: true }, { owner: 'acme.theme' });
    applyTheme('acme.dark', root);
    expect(root.style.getPropertyValue('--nm-ink')).toBe('#fff');
    expect(root.dataset.pluginTheme).toBe('acme.dark');
    clearTheme(root);
    expect(root.style.getPropertyValue('--nm-ink')).toBe('');
    expect(root.dataset.pluginTheme).toBeUndefined();
    expect(() => applyTheme('nope', root)).toThrow(/not registered/);
  });
});
