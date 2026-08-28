/**
 * @file_name: AgentModelChip.tsx
 * @author:
 * @date: 2026-08-26
 * @description: Compact collapsed-row chip showing an agent's effective main
 *   (agent-slot) model + an inherit/override marker. Pure/presentational — the
 *   data comes from DashboardPage's single bulk overview fetch (no per-chip
 *   request). Renders nothing when the agent has no overview entry yet.
 */
import { useTranslation } from 'react-i18next';
import type { AgentModelOverview } from '@/types/api';

interface Props {
  agentId: string;
  entry?: AgentModelOverview[string];
}

export function AgentModelChip({ agentId, entry }: Props) {
  const { t } = useTranslation();
  if (!entry) return null;
  const inheriting = entry.agent.inheriting;
  return (
    <span
      data-testid={`model-chip-${agentId}`}
      className="mt-0.5 inline-flex items-center gap-1 text-[10px] w-fit max-w-full"
    >
      <span className="font-mono truncate max-w-[120px] text-[var(--nm-ink70)]">
        {entry.agent.model || '—'}
      </span>
      <span className={inheriting ? 'text-[var(--nm-ink30)]' : 'text-[var(--color-warning)]'}>
        {inheriting
          ? t('pages.dashboard.modelChip.inherit', 'default')
          : t('pages.dashboard.modelChip.override', 'custom')}
      </span>
    </span>
  );
}
