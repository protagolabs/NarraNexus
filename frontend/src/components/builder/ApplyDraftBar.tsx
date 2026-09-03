/**
 * @file_name: ApplyDraftBar.tsx
 * @author: NetMind.AI
 * @date: 2026-09-03
 * @description: The one place a builder draft can become the agent's actual
 * instructions.
 *
 * v0 never writes Awareness on the agent's behalf — the draft lives in an
 * artifact and stays inert until the user presses this. That is a product
 * rule, not a limitation: the conversation runs on the user's own agent, so
 * an automatic write would be us editing their configuration because a model
 * suggested something.
 *
 * Shown only for the artifact the builder instruction names
 * (`agent-config`, text/markdown). A model that ignores the convention
 * therefore costs the user a manual copy rather than putting an apply button
 * on an unrelated document — the failure mode is inconvenience, never a
 * wrong write.
 *
 * Disabled while the markdown editor has unsaved keystrokes. The bar reads
 * the artifact's saved bytes over the raw URL, not the editor's buffer, so
 * applying mid-debounce would silently write the version BEFORE the user's
 * last edit. Waiting for autosave (blur + idle) is the honest behaviour;
 * claiming to have applied edits we did not read is not.
 */
import { useCallback, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Check, Loader2, Wand2 } from 'lucide-react';
import { api } from '@/lib/api';
import { Button, useConfirm } from '@/components/ui';
import { useArtifactRawUrl } from '@/hooks/useArtifactRawUrl';
import { useArtifactStore } from '@/stores';
import type { Artifact } from '@/types/artifact';

type Status = 'idle' | 'applying' | 'done' | 'error';

export function ApplyDraftBar({ artifact }: { artifact: Artifact }) {
  const { t } = useTranslation();
  const { confirm, dialog } = useConfirm();
  const { url } = useArtifactRawUrl(artifact.agent_id, artifact.artifact_id, artifact.updated_at);
  const dirty = useArtifactStore((s) => s.editorDirtyIds.has(artifact.artifact_id));
  const [status, setStatus] = useState<Status>('idle');
  const [error, setError] = useState<string | null>(null);

  const apply = useCallback(async () => {
    if (!url) return;
    setError(null);

    let draft = '';
    try {
      const res = await fetch(url);
      if (!res.ok) throw new Error(`fetch failed: ${res.status}`);
      draft = (await res.text()).trim();
    } catch (e) {
      setStatus('error');
      setError(t('builder.apply.readFailed', { detail: String(e) }));
      return;
    }
    if (!draft) {
      setStatus('error');
      setError(t('builder.apply.emptyDraft'));
      return;
    }

    // Overwriting instructions is not recoverable from the UI, so it is
    // confirmed even though the draft is what the user came here to apply.
    const ok = await confirm({
      title: t('builder.apply.confirmTitle'),
      message: t('builder.apply.confirmBody'),
      confirmText: t('builder.apply.confirmCta'),
    });
    if (!ok) return;

    setStatus('applying');
    try {
      const res = await api.updateAwareness(artifact.agent_id, draft);
      if (!res.success) throw new Error(res.message ?? res.error ?? 'update failed');
      setStatus('done');
    } catch (e) {
      setStatus('error');
      setError(t('builder.apply.writeFailed', { detail: String(e) }));
    }
  }, [url, confirm, artifact.agent_id, t]);

  return (
    <div
      className="flex items-center gap-3 px-3 py-2.5 border-b"
      style={{ borderColor: 'var(--nm-hairline)' }}
    >
      <Button
        size="sm"
        disabled={!url || dirty || status === 'applying'}
        onClick={() => void apply()}
        className="gap-1.5 shrink-0"
      >
        {status === 'applying' ? (
          <Loader2 className="w-3.5 h-3.5 animate-spin" />
        ) : status === 'done' ? (
          <Check className="w-3.5 h-3.5" />
        ) : (
          <Wand2 className="w-3.5 h-3.5" />
        )}
        {status === 'done' ? t('builder.apply.applied') : t('builder.apply.cta')}
      </Button>

      <p className="text-[11.5px] leading-snug min-w-0" style={{ color: 'var(--text-tertiary)' }}>
        {status === 'error'
          ? error
          : status === 'done'
            ? t('builder.apply.doneHint')
            : dirty
              ? t('builder.apply.savingHint')
              : t('builder.apply.hint')}
      </p>
      {dialog}
    </div>
  );
}
