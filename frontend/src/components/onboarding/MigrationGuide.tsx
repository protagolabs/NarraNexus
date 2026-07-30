/**
 * @file_name: MigrationGuide.tsx
 * @author: NetMind.AI
 * @date: 2026-07-30
 * @description: One-time guided flow that offers to import other-framework agents
 * on the local/desktop app. Replaces the old MigrationNudge banner.
 *
 * Flow (per-user, see lib/migrationGuide.ts):
 *   not welcomed → detect → if any agent found, show the welcome modal (once)
 *     [Import]   → mark welcomed → open ImportAgentModal
 *     [Later]/X  → mark welcomed + coachmarkPending → show the coach-mark
 *   welcomed & coachmarkPending & !coachmarkDone → coach-mark pointing at "+"
 *     (clicked away → coachmarkDone, gone for good — survives reloads until then)
 *
 * LOCAL ONLY: detect reads the user's filesystem (503s on cloud), so the whole
 * flow is gated on `mode === 'local'`.
 */

import { useState, useEffect } from 'react';
import { useTranslation } from 'react-i18next';
import { useNavigate } from 'react-router-dom';
import { Download } from 'lucide-react';
import { Dialog, DialogContent, DialogFooter, Button } from '@/components/ui';
import { useRuntimeStore, useConfigStore, useChatStore } from '@/stores';
import { api } from '@/lib/api';
import {
  readMigrationGuide,
  writeMigrationGuide,
  type MigrationGuideState,
} from '@/lib/migrationGuide';
import type { FrameworkDetection, MigrationApplyResult } from '@/types';
import { ImportAgentModal } from '@/components/layout/ImportAgentModal';
import { MigrationCoachmark } from './MigrationCoachmark';

const FRAMEWORK_LABEL: Record<string, string> = {
  claude_code: 'Claude Code',
  hermes: 'Hermes',
  openclaw: 'OpenClaw',
  codex: 'Codex',
  custom: 'Custom',
};

export function MigrationGuide() {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const isLocal = useRuntimeStore((s) => s.mode) === 'local';
  const userId = useConfigStore((s) => s.userId);

  const [detections, setDetections] = useState<FrameworkDetection[]>([]);
  const [importOpen, setImportOpen] = useState(false);
  // Setter-only re-render trigger: patch() writes localStorage then bumps this so
  // the direct read below re-runs. Reading localStorage in render keeps it always
  // fresh without a setState-in-effect on load.
  const [, forceRefresh] = useState(0);

  const state: MigrationGuideState | null =
    isLocal && userId ? readMigrationGuide(userId) : null;
  const notWelcomed = !!state && !state.welcomed;

  // Detect only while not yet welcomed (setDetections runs in an async callback,
  // so this doesn't trip set-state-in-effect).
  useEffect(() => {
    if (!isLocal || !userId || !notWelcomed) return;
    let alive = true;
    api
      .migrateDetect()
      .then((res) => { if (alive) setDetections(res.detections); })
      .catch(() => { /* best-effort; silent on failure */ });
    return () => { alive = false; };
  }, [isLocal, userId, notWelcomed]);

  if (!isLocal || !userId || !state) return null;

  const patch = (p: Partial<MigrationGuideState>) => {
    writeMigrationGuide(userId, p);
    forceRefresh((n) => n + 1);
  };

  // "Real" agents = exclude the global-shared-config fallback row.
  const real = detections.filter((d) => !d.signals.includes('global-shared-config'));
  const byFramework = Array.from(
    real.reduce((m, d) => m.set(d.framework, (m.get(d.framework) ?? 0) + 1), new Map<string, number>()),
  ).map(([fw, n]) => `${FRAMEWORK_LABEL[fw] ?? fw} · ${n}`);

  const handleApplied = async (result: MigrationApplyResult) => {
    await useConfigStore.getState().refreshAgents().catch(() => { /* best-effort */ });
    useConfigStore.getState().setAgentId(result.agent_id);
    useChatStore.getState().setActiveAgent(result.agent_id);
    navigate('/app/chat');
  };

  const showModal = !state.welcomed && real.length > 0 && !importOpen;
  const showCoachmark = state.welcomed && state.coachmarkPending && !state.coachmarkDone;

  return (
    <>
      {showModal && (
        <Dialog
          isOpen
          onClose={() => patch({ welcomed: true, coachmarkPending: true })}
          title={t('onboarding.migrationGuide.title')}
          size="md"
        >
          <DialogContent>
            <div className="flex items-start gap-3 text-sm">
              <Download className="mt-0.5 h-5 w-5 shrink-0 text-[var(--nm-ink-soft)]" />
              <div>
                <p className="text-[var(--nm-ink)]">
                  {t('onboarding.migrationGuide.body', { count: real.length })}
                </p>
                <ul className="mt-2 space-y-0.5 text-xs text-[var(--nm-ink-soft)]">
                  {byFramework.map((line) => (
                    <li key={line}>· {line}</li>
                  ))}
                </ul>
              </div>
            </div>
          </DialogContent>
          <DialogFooter>
            <Button
              variant="ghost"
              onClick={() => patch({ welcomed: true, coachmarkPending: true })}
            >
              {t('onboarding.migrationGuide.later')}
            </Button>
            <Button
              onClick={() => { patch({ welcomed: true }); setImportOpen(true); }}
            >
              {t('onboarding.migrationGuide.import')}
            </Button>
          </DialogFooter>
        </Dialog>
      )}

      {showCoachmark && (
        <MigrationCoachmark onDismiss={() => patch({ coachmarkDone: true })} />
      )}

      {importOpen && (
        <ImportAgentModal onClose={() => setImportOpen(false)} onApplied={handleApplied} />
      )}
    </>
  );
}
