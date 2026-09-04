/**
 * @file_name: themes.ts
 * @author: Bin Liang
 * @date: 2026-09-03
 * @description: Theme registry — a theme overrides design tokens the shell declared in `@theme`, nothing else.
 *
 * A plugin theme is data: token → value. Registration validates every key
 * against the generated token list (`themeTokens.generated.ts`, produced
 * from `index.css`), so a theme cannot invent variables the components do
 * not read or smuggle arbitrary CSS. `applyTheme` sets the variables on the
 * root element; `clearTheme` removes exactly what was set.
 */
import { Registry } from './registry';
import { THEME_TOKENS } from '../themeTokens.generated';

export interface ThemeDef {
  displayName: string;
  tokens: Record<string, string>;
  dark?: boolean;
}

const KNOWN = new Set<string>(THEME_TOKENS);
const SAFE_VALUE = /^[\w\s#%.,()/+\-"']*$/;

export function validateThemeTokens(tokens: Record<string, string>): string[] {
  const problems: string[] = [];
  for (const [key, value] of Object.entries(tokens)) {
    if (!KNOWN.has(key)) problems.push(`unknown token ${key}`);
    else if (!SAFE_VALUE.test(value) || value.includes('url(')) problems.push(`unsafe value for ${key}`);
  }
  return problems;
}

class ThemeRegistry extends Registry<ThemeDef> {
  override register(id: string, value: ThemeDef, options = {}) {
    const problems = validateThemeTokens(value.tokens);
    if (problems.length) throw new Error(`ui.themes: "${id}": ${problems.join('; ')}`);
    return super.register(id, value, options);
  }
}

export const THEMES = new ThemeRegistry('ui.themes');

let applied: string[] = [];

export function applyTheme(id: string, root: HTMLElement = document.documentElement): void {
  const theme = THEMES.get(id);
  if (!theme) throw new Error(`ui.themes: "${id}" is not registered`);
  clearTheme(root);
  for (const [key, value] of Object.entries(theme.tokens)) {
    root.style.setProperty(key, value);
    applied.push(key);
  }
  root.dataset.pluginTheme = id;
}

export function clearTheme(root: HTMLElement = document.documentElement): void {
  for (const key of applied) root.style.removeProperty(key);
  applied = [];
  delete root.dataset.pluginTheme;
}
