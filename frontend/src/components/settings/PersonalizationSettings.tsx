/**
 * @file_name: PersonalizationSettings.tsx
 * @author: NarraNexus
 * @date: 2026-08-19
 * @description: The Settings "Personalization" pane — theme and language.
 *
 * These two controls live here, in Settings, and nowhere else. They used to
 * hide inside the sidebar's account popover, which made the popover a second
 * settings surface and left users unsure what the difference between the two
 * was. The account popover now carries identity only (account / version /
 * logout); everything configurable belongs to this page.
 */

import { useTranslation } from 'react-i18next';
import { Monitor, Sun, Moon, Check } from 'lucide-react';
import { useTheme } from '@/hooks';
import { SUPPORTED_LANGUAGES } from '@/i18n';
import { cn } from '@/lib/utils';

const THEME_OPTIONS = [
  { value: 'system', icon: Monitor, labelKey: 'sidebar.themeSystem' },
  { value: 'light', icon: Sun, labelKey: 'sidebar.themeLight' },
  { value: 'dark', icon: Moon, labelKey: 'sidebar.themeDark' },
] as const;

export function PersonalizationSettings() {
  const { t, i18n } = useTranslation();
  const { theme, setTheme } = useTheme();
  const currentLang =
    SUPPORTED_LANGUAGES.find(
      (l) => i18n.resolvedLanguage === l.code || i18n.language?.startsWith(l.code),
    ) ?? SUPPORTED_LANGUAGES[0];

  return (
    <div className="space-y-8">
      {/* Theme */}
      <div>
        <h3 className="text-sm font-medium text-[var(--nm-ink)] mb-2">
          {t('sidebar.theme')}
        </h3>
        <div className="flex gap-2" role="radiogroup" aria-label={t('sidebar.theme')}>
          {THEME_OPTIONS.map(({ value, icon: Icon, labelKey }) => {
            const active = theme === value;
            return (
              <button
                key={value}
                type="button"
                role="radio"
                aria-checked={active}
                onClick={() => setTheme(value)}
                className={cn(
                  'flex items-center gap-2 px-4 py-2 rounded-[var(--radius-md)] border text-sm transition-colors',
                  active
                    ? 'border-[var(--accent-primary)] text-[var(--accent-primary)] bg-[var(--accent-primary)]/8'
                    : 'border-[var(--border-default)] text-[var(--nm-ink70)] hover:border-[var(--border-strong)]',
                )}
              >
                <Icon className="w-4 h-4" />
                {t(labelKey)}
              </button>
            );
          })}
        </div>
      </div>

      {/* Language */}
      <div>
        <h3 className="text-sm font-medium text-[var(--nm-ink)] mb-2">
          {t('sidebar.language')}
        </h3>
        <div
          className="max-w-sm rounded-[var(--radius-md)] border border-[var(--border-default)] overflow-hidden"
          role="radiogroup"
          aria-label={t('sidebar.language')}
        >
          {SUPPORTED_LANGUAGES.map((l) => {
            const active = l.code === currentLang.code;
            return (
              <button
                key={l.code}
                type="button"
                role="radio"
                aria-checked={active}
                dir={l.code === 'ar' ? 'rtl' : undefined}
                onClick={() => void i18n.changeLanguage(l.code)}
                className={cn(
                  'w-full flex items-center gap-2.5 px-3 py-2 text-sm text-left transition-colors',
                  'border-b border-[var(--nm-hairline)] last:border-b-0',
                  'hover:bg-[var(--nm-paper-warm)]',
                  active ? 'text-[var(--nm-ink)] font-medium' : 'text-[var(--nm-ink70)]',
                )}
              >
                <span className="text-base leading-none">{l.flag}</span>
                <span className="flex-1">{l.label}</span>
                {active && <Check className="w-3.5 h-3.5 shrink-0" />}
              </button>
            );
          })}
        </div>
      </div>
    </div>
  );
}
