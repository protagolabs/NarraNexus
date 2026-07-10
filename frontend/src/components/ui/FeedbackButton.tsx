/**
 * @file_name: FeedbackButton.tsx
 * @author:
 * @date: 2026-07-10
 * @description: Floating feedback entry — sits directly above the
 * bottom-right help "?" button (Owner moved it here from the sidebar
 * footer, 2026-07-10) and shares its visual language: same circle,
 * same card/hairline tokens, stacked at bottom-14 vs help's bottom-4.
 * Owns the FeedbackDialog open state.
 */

import { useState } from 'react';
import { useTranslation } from 'react-i18next';
import { MessageSquarePlus } from 'lucide-react';
import { FeedbackDialog } from './FeedbackDialog';

export function FeedbackButton() {
  const { t } = useTranslation();
  const [open, setOpen] = useState(false);

  return (
    <>
      <button
        type="button"
        aria-label={t('feedback.title')}
        title={t('feedback.title')}
        onClick={() => setOpen(true)}
        className="fixed bottom-14 right-4 z-[150] flex items-center justify-center w-8 h-8 rounded-full cursor-pointer transition-all duration-150 hover:scale-110"
        style={{
          background: 'var(--nm-card)',
          border: '1px solid var(--nm-hairline)',
          boxShadow: 'var(--nm-elev-1)',
          color: 'var(--nm-ink50)',
        }}
      >
        <MessageSquarePlus className="w-4 h-4" aria-hidden />
      </button>
      <FeedbackDialog isOpen={open} onClose={() => setOpen(false)} />
    </>
  );
}
