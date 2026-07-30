/**
 * @file_name: MigrationNudge.tsx
 * @author: NetMind.AI
 * @date: 2026-07-30
 * @description: One-time local-mode nudge to import other-framework agents.
 *
 * On the local/desktop app, after login, we scan the machine for other-framework
 * agents (Claude Code / Codex / OpenClaw / Hermes) and, if any are found, offer a
 * dismissible card that deep-links into the ImportAgentModal. Cloud never mounts
 * this (the backend has no user filesystem — detect 503s there anyway).
 *
 * Dismissal is per-machine (localStorage): detection is filesystem-local, so a
 * user on another machine should be nudged there too.
 */

import { useState, useEffect } from 'react';
import { useTranslation } from 'react-i18next';
import { useNavigate } from 'react-router-dom';
import { Download, X } from 'lucide-react';
import { Button } from '@/components/ui';
import { useRuntimeStore, useConfigStore, useChatStore } from '@/stores';
import { api } from '@/lib/api';
import type { FrameworkDetection, MigrationApplyResult } from '@/types';
import { ImportAgentModal } from '@/components/layout/ImportAgentModal';

const DISMISS_KEY = 'nn_migration_nudge_dismissed_v1';

const FRAMEWORK_LABEL: Record<string, string> = {
  claude_code: 'Claude Code',
  hermes: 'Hermes',
  openclaw: 'OpenClaw',
  codex: 'Codex',
  custom: 'Custom',
};

export function MigrationNudge() {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const isLocal = useRuntimeStore((s) => s.mode) === 'local';
  const userId = useConfigStore((s) => s.userId);

  const [detections, setDetections] = useState<FrameworkDetection[]>([]);
  const [dismissed, setDismissed] = useState(
    () => localStorage.getItem(DISMISS_KEY) === '1',
  );
  const [open, setOpen] = useState(false);

  useEffect(() => {
    if (!isLocal || !userId || dismissed) return;
    let alive = true;
    api
      .migrateDetect()
      .then((res) => { if (alive) setDetections(res.detections); })
      .catch(() => { /* detect is best-effort; stay silent on failure */ });
    return () => { alive = false; };
  }, [isLocal, userId, dismissed]);

  const dismiss = () => {
    localStorage.setItem(DISMISS_KEY, '1');
    setDismissed(true);
  };

  const handleApplied = async (result: MigrationApplyResult) => {
    await useConfigStore.getState().refreshAgents().catch(() => { /* best-effort */ });
    useConfigStore.getState().setAgentId(result.agent_id);
    useChatStore.getState().setActiveAgent(result.agent_id);
    dismiss();
    navigate('/app/chat');
  };

  if (!isLocal || dismissed || detections.length === 0) return null;

  // Distinct framework labels found (dedupe the per-project Claude rows).
  const frameworks = Array.from(new Set(detections.map((d) => d.framework)))
    .map((fw) => FRAMEWORK_LABEL[fw] ?? fw);

  return (
    <>
      <div className="m-3 flex items-start gap-3 rounded-[var(--radius-sm)] border border-[var(--nm-hairline)] bg-[var(--nm-paper-warm)] px-4 py-3">
        <Download className="mt-0.5 h-4 w-4 shrink-0 text-[var(--nm-ink-soft)]" />
        <div className="min-w-0 flex-1">
          <div className="text-sm font-medium text-[var(--nm-ink)]">
            {t('onboarding.migrationNudge.title')}
          </div>
          <div className="mt-0.5 text-xs text-[var(--nm-ink-soft)]">
            {t('onboarding.migrationNudge.body', { frameworks: frameworks.join(', ') })}
          </div>
          <div className="mt-2 flex gap-2">
            <Button size="sm" onClick={() => setOpen(true)}>
              {t('onboarding.migrationNudge.import')}
            </Button>
            <Button size="sm" variant="ghost" onClick={dismiss}>
              {t('onboarding.migrationNudge.dismiss')}
            </Button>
          </div>
        </div>
        <button
          onClick={dismiss}
          className="shrink-0 text-[var(--nm-ink-soft)] hover:text-[var(--nm-ink)]"
          aria-label={t('onboarding.migrationNudge.dismiss')}
        >
          <X className="h-3.5 w-3.5" />
        </button>
      </div>

      {open && (
        <ImportAgentModal onClose={() => setOpen(false)} onApplied={handleApplied} />
      )}
    </>
  );
}
