/**
 * @file_name: AgentModelCard.tsx
 * @author:
 * @date: 2026-08-26
 * @description: Read-only per-agent model config card for the Dashboard
 *   expanded row (agent + helper_llm effective model, inherit/override badge),
 *   with an Edit button that opens the shared AgentLlmConfigPanel. Reads the
 *   existing per-agent llm-config endpoint lazily (only when a row expands).
 */
import { useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { api } from '@/lib/api';
import type { AgentSlotView } from '@/types/api';

interface Props {
  agentId: string;
  reloadKey: number; // bump to refetch after an edit saves
  onEdit: () => void;
}

const SLOTS: Array<{ key: 'agent' | 'helper_llm'; labelKey: string; fallback: string }> = [
  { key: 'agent', labelKey: 'pages.dashboard.modelCard.agentSlot', fallback: 'Main (agent)' },
  { key: 'helper_llm', labelKey: 'pages.dashboard.modelCard.helperSlot', fallback: 'Helper' },
];

export function AgentModelCard({ agentId, reloadKey, onEdit }: Props) {
  const { t } = useTranslation();
  const [slots, setSlots] = useState<Record<string, AgentSlotView> | null>(null);

  useEffect(() => {
    let alive = true;
    api
      .getAgentLlmConfig(agentId)
      .then((r) => {
        if (alive && r.success && r.data) setSlots(r.data.slots);
      })
      .catch(() => {
        /* leave null → show dashes */
      });
    return () => {
      alive = false;
    };
  }, [agentId, reloadKey]);

  return (
    <div className="border-t border-[var(--nm-hairline)] pt-3">
      <div className="flex items-center justify-between mb-2">
        <span className="text-[12px] font-semibold text-[var(--nm-ink70)]">
          {t('pages.dashboard.modelCard.title', 'Model config')}
        </span>
        <button
          data-testid="agent-model-edit"
          onClick={onEdit}
          className="text-[12px] text-[var(--nm-accent)] hover:underline"
        >
          {t('common.edit', 'Edit')}
        </button>
      </div>
      <div className="space-y-1">
        {SLOTS.map(({ key, labelKey, fallback }) => {
          const view = slots?.[key];
          const eff = view?.effective;
          const inheriting = view?.inheriting;
          return (
            <div key={key} className="flex items-center gap-2 text-[12px]">
              <span className="w-16 text-[var(--nm-ink50)]">{t(labelKey, fallback)}</span>
              <span className="font-mono text-[var(--nm-ink)]">{eff?.model || '—'}</span>
              {eff?.reasoning_effort && (
                <span className="text-[var(--nm-ink50)]">
                  {t('pages.dashboard.modelCard.effort', 'effort={{value}}', { value: eff.reasoning_effort })}
                </span>
              )}
              {view &&
                (inheriting ? (
                  <span
                    data-testid={`${key}-slot-inherit`}
                    className="text-[10px] text-[var(--nm-ink50)]"
                  >
                    {t('pages.dashboard.modelCard.inherit', 'inherits default')}
                  </span>
                ) : (
                  <span
                    data-testid={`${key}-slot-override`}
                    className="text-[10px] text-[var(--color-warning)]"
                  >
                    {t('pages.dashboard.modelCard.override', 'overridden')}
                  </span>
                ))}
            </div>
          );
        })}
      </div>
    </div>
  );
}
