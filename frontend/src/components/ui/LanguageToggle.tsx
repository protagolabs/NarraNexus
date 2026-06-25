/**
 * Language toggle — cycles through the supported UI languages.
 *
 * Lives next to ThemeToggle in the sidebar footer. Cycling is fine for the
 * current small set (EN / 中文); when the language list grows this can become
 * a popover/dropdown driven by the same SUPPORTED_LANGUAGES source.
 */
import { Languages } from 'lucide-react';
import { useTranslation } from 'react-i18next';

import { Button } from './Button';
import { cn } from '@/lib/utils';
import { SUPPORTED_LANGUAGES } from '@/i18n';

export function LanguageToggle() {
  const { i18n } = useTranslation();

  const codes = SUPPORTED_LANGUAGES.map((l) => l.code);
  const current =
    SUPPORTED_LANGUAGES.find(
      (l) => i18n.resolvedLanguage === l.code || i18n.language?.startsWith(l.code),
    ) ?? SUPPORTED_LANGUAGES[0];

  const cycle = () => {
    const idx = codes.indexOf(current.code);
    const next = codes[(idx + 1) % codes.length];
    void i18n.changeLanguage(next);
  };

  return (
    <Button
      variant="ghost"
      size="icon"
      onClick={cycle}
      title={`Language: ${current.label}`}
      aria-label={`Language: ${current.label}`}
      className="relative gap-1"
    >
      <Languages className={cn('h-4 w-4 transition-colors duration-150')} />
      <span className="text-[9px] font-mono uppercase tracking-wider">{current.code}</span>
    </Button>
  );
}
