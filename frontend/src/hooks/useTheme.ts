/**
 * Theme hook — thin selector wrapper over useThemeStore.
 *
 * Exposes the same shape used across the app: { theme, effectiveTheme,
 * setTheme, toggleTheme, isDark }. State lives in the Zustand store so all
 * subscribers stay in sync when the toggle flips.
 */

import { useThemeStore } from '@/stores/themeStore';

export function useTheme() {
  const theme = useThemeStore((s) => s.theme);
  const effectiveTheme = useThemeStore((s) => s.effectiveTheme);
  const setTheme = useThemeStore((s) => s.setTheme);
  const toggleTheme = useThemeStore((s) => s.toggleTheme);

  return {
    theme,
    effectiveTheme,
    setTheme,
    toggleTheme,
    isDark: effectiveTheme === 'dark',
  };
}
