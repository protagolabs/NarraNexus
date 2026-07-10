/**
 * FeedbackDialog — manual product-feedback entry (spec 2026-07-10).
 *
 * The explicit fallback channel next to the agent's automatic
 * submit_feedback tool: category select + free text, relayed by the
 * backend to the team's feedback intake. Fire-and-forget UX — we thank
 * the user even if the intake is unreachable (delivered=false).
 */

import { useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Dialog } from './Dialog';
import { Button } from './Button';
import { api } from '@/lib/api';

const CATEGORIES = ['user_dissatisfaction', 'feature_gap', 'error', 'other'] as const;

interface FeedbackDialogProps {
  isOpen: boolean;
  onClose: () => void;
}

export function FeedbackDialog({ isOpen, onClose }: FeedbackDialogProps) {
  const { t } = useTranslation();
  const [category, setCategory] = useState<string>('other');
  const [text, setText] = useState('');
  const [busy, setBusy] = useState(false);
  const [done, setDone] = useState(false);

  const reset = () => {
    setText('');
    setCategory('other');
    setDone(false);
    setBusy(false);
  };

  const handleClose = () => {
    reset();
    onClose();
  };

  const handleSubmit = async () => {
    if (!text.trim() || busy) return;
    setBusy(true);
    try {
      await api.submitFeedback(category, text.trim().slice(0, 500));
    } catch {
      // Feedback must never error at the user — treat as sent.
    }
    setDone(true);
    setBusy(false);
  };

  return (
    <Dialog isOpen={isOpen} onClose={handleClose} title={t('feedback.title')} size="md">
      {done ? (
        <div className="space-y-4">
          <p className="text-sm">{t('feedback.thanks')}</p>
          <div className="flex justify-end">
            <Button onClick={handleClose}>{t('feedback.close')}</Button>
          </div>
        </div>
      ) : (
        <div className="space-y-4">
          <div>
            <label className="block text-xs font-mono uppercase mb-1 text-[var(--text-secondary)]">
              {t('feedback.categoryLabel')}
            </label>
            <select
              value={category}
              onChange={(e) => setCategory(e.target.value)}
              className="w-full border border-[var(--border-default)] bg-transparent px-2 py-1.5 text-sm"
            >
              {CATEGORIES.map((c) => (
                <option key={c} value={c}>{t(`feedback.categories.${c}`)}</option>
              ))}
            </select>
          </div>
          <div>
            <label className="block text-xs font-mono uppercase mb-1 text-[var(--text-secondary)]">
              {t('feedback.textLabel')}
            </label>
            <textarea
              value={text}
              onChange={(e) => setText(e.target.value)}
              maxLength={500}
              rows={5}
              placeholder={t('feedback.placeholder')}
              className="w-full border border-[var(--border-default)] bg-transparent px-2 py-1.5 text-sm resize-none"
            />
            <div className="text-right text-[10px] text-[var(--text-tertiary)]">{text.length}/500</div>
          </div>
          <div className="flex justify-end gap-2">
            <Button variant="ghost" onClick={handleClose}>{t('feedback.cancel')}</Button>
            <Button onClick={handleSubmit} disabled={!text.trim() || busy}>
              {busy ? t('feedback.sending') : t('feedback.submit')}
            </Button>
          </div>
        </div>
      )}
    </Dialog>
  );
}
