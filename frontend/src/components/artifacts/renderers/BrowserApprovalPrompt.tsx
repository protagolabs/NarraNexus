/**
 * @file_name: BrowserApprovalPrompt.tsx
 * @date: 2026-09-22
 * @description: Explicit decisions for independent privileged browser capabilities.
 *
 * The other half of an `ask` verdict. Without it the agent reports
 * NEEDS_HUMAN and there is nothing the user can do about it.
 *
 * Every choice here is about not training people to click yes:
 *
 * - The **exact origin** is the headline, at full size. "A website wants
 *   access" is a prompt nobody can evaluate, so nobody reads it.
 * - The **capability** is named. Ordinary site browsing has no approval prompt.
 *   Approving a download does not approve an upload.
 * - There is **no default button and no Enter-to-accept**. A prompt you can
 *   dismiss without reading is a prompt that grants without consent.
 * - "Always" is visually the *quietest* option, not the loudest. The easy
 *   click should be the narrow grant.
 */
import { useId, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Ban, Check, Globe, ShieldQuestion } from 'lucide-react';

import type { ApprovalLifetime, PendingApproval } from '@/types/browser';
import { Button } from '@/components/nm/button';

interface Props {
  approval: PendingApproval;
  onDecide: (decision: 'allow' | 'deny', lifetime: ApprovalLifetime) => Promise<void>;
}

export default function BrowserApprovalPrompt({ approval, onDecide }: Props) {
  const { t } = useTranslation();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const inFlight = useRef(false);
  const id = useId();
  // Never infer turn or conversation context. Permanent policy decisions
  // are the only choices that do not require either context identifier.
  const allowedLifetimes = approval.allowed_lifetimes ?? ['always'];

  // NOTE: every button below is an explicit type="button". An HTML button
  // defaults to type="submit", which inside a form is activated by Enter —
  // so a prompt the user never read could be answered by a stray keypress.

  const decide = async (decision: 'allow' | 'deny', lifetime: ApprovalLifetime) => {
    if (inFlight.current || !allowedLifetimes.includes(lifetime)) return;
    inFlight.current = true;
    setBusy(true);
    setError(null);
    try {
      await onDecide(decision, lifetime);
    } catch (e) {
      setError(e instanceof Error && e.message ? e.message
        : t('browser.approval.decisionFailed', 'Could not save the decision. Please try again.'));
    } finally {
      inFlight.current = false;
      setBusy(false);
    }
  };

  const capabilityLabel =
    approval.capability === 'uploads'
      ? t('browser.approval.capUploads', 'upload files to')
      : approval.capability === 'downloads'
        ? t('browser.approval.capDownloads', 'download files from')
        : t('browser.approval.capAutoReview', 'automatically review actions on');

  return (
    <div
      className="flex flex-col gap-3 p-4 border-b border-[var(--border-default)] bg-[var(--nm-paper-warm)]"
      data-testid="browser-approval"
      role="group"
      aria-labelledby={`${id}-question ${id}-origin`}
      aria-describedby={error ? `${id}-error` : undefined}
    >
      <div className="flex items-start gap-2">
        <ShieldQuestion className="w-4 h-4 mt-0.5 shrink-0 opacity-70" />
        <div className="min-w-0">
          <div id={`${id}-question`} className="text-xs text-[var(--text-secondary)]">
            {t('browser.approval.question', 'The agent wants to {{action}}:', {
              action: capabilityLabel,
            })}
          </div>
          {/* The origin is the whole decision — shown big, whole, and not
              truncated into something that could hide the real host. */}
          <div
            className="mt-1 flex items-center gap-1.5 text-sm font-medium break-all"
            data-testid="browser-approval-origin"
            id={`${id}-origin`}
          >
            <Globe className="w-3.5 h-3.5 shrink-0 opacity-70" />
            <span className="min-w-0 break-all">{approval.origin}</span>
          </div>
        </div>
      </div>

      <div className="flex flex-wrap items-center gap-2">
        {(['turn', 'thread'] as const).filter((lifetime) => allowedLifetimes.includes(lifetime)).map((lifetime) => (
          <Button
            key={lifetime}
            type="button"
            variant="secondary"
            size="sm"
            leading={<Check className="h-3.5 w-3.5 shrink-0" />}
            disabled={busy}
            onClick={() => void decide('allow', lifetime)}
            data-testid={`browser-approval-allow-${lifetime}`}
            className="h-auto min-h-8 max-w-full py-1.5"
          >
            {lifetime === 'turn'
              ? t('browser.approval.allowTurn', 'Allow for this turn')
              : t('browser.approval.allowThread', 'Allow for this conversation')}
          </Button>
        ))}
        {allowedLifetimes.includes('always') && <Button
          type="button"
          variant="secondary"
          size="sm"
          leading={<Ban className="h-3.5 w-3.5 shrink-0" />}
          disabled={busy}
          onClick={() => void decide('deny', 'always')}
          data-testid="browser-approval-deny"
          className="h-auto min-h-8 max-w-full py-1.5"
        >
          {t('browser.approval.deny', 'Never allow')}
        </Button>}
        {/* Quietest choice on purpose: the easiest click should be the
            narrowest grant, not the broadest. */}
        {allowedLifetimes.includes('always') && <Button
          type="button"
          variant="ghost"
          size="sm"
          disabled={busy}
          onClick={() => void decide('allow', 'always')}
          data-testid="browser-approval-allow-always"
          className="h-auto min-h-8 max-w-full py-1.5 text-[var(--text-secondary)] underline-offset-2 hover:underline"
        >
          {t('browser.approval.allowAlways', 'Always allow this site')}
        </Button>}
      </div>
      {busy && <p role="status" className="text-xs text-[var(--text-secondary)]">
        {t('browser.approval.saving', 'Saving decision...')}
      </p>}
      {error && <p id={`${id}-error`} role="alert" className="break-words text-xs text-[var(--color-error)]">
        {error}
      </p>}
    </div>
  );
}
