/**
 * @file_name: AgentCapabilitiesPanel.tsx
 * @author: Bin Liang
 * @date: 2026-09-04
 * @description: Per-agent capability switches (plugin platform batch 5c) — which registered modules take part in this agent's turns.
 *
 * Builtin modules are on by default, plugin-installed modules off (context
 * budget), base modules are locked on. Every toggle writes immediately
 * (/api/agents/{id}/capabilities/{module}); a change applies on the agent's
 * next run. The budget line compares the enabled set's declared prompt cost
 * with the builtin baseline and turns amber past 2×.
 */
import { useCallback, useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Loader2, Lock } from 'lucide-react';

import { Dialog, DialogContent } from '@/components/ui';
import { api } from '@/lib/api';
import type { AgentCapabilitiesView, AgentCapabilityItem } from '@/types';

interface Props {
  agentId: string;
  isOpen: boolean;
  onClose: () => void;
}

export function AgentCapabilitiesPanel({ agentId, isOpen, onClose }: Props) {
  const { t } = useTranslation();
  const [view, setView] = useState<AgentCapabilitiesView | null>(null);
  const [loading, setLoading] = useState(false);
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState('');

  const load = useCallback(async () => {
    setLoading(true);
    setError('');
    try {
      const res = await api.getAgentCapabilities(agentId);
      if (res.success && res.data) setView(res.data);
      else setError(res.detail || t('chat.capabilities.loadFailed'));
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : t('chat.capabilities.loadFailed'));
    } finally {
      setLoading(false);
    }
  }, [agentId, t]);

  useEffect(() => {
    if (isOpen) void load();
  }, [isOpen, load]);

  const toggle = async (item: AgentCapabilityItem) => {
    if (item.locked || busy) return;
    setBusy(item.module_class);
    setError('');
    try {
      const res = await api.setAgentCapability(agentId, item.module_class, !item.enabled);
      if (!res.success) setError(res.detail || t('chat.capabilities.saveFailed'));
      await load();
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : t('chat.capabilities.saveFailed'));
    } finally {
      setBusy(null);
    }
  };

  return (
    <Dialog isOpen={isOpen} onClose={onClose} title={t('chat.capabilities.title')} size="md">
      <DialogContent>
        <p className="text-[12px] text-[var(--nm-ink50)] mb-3">{t('chat.capabilities.subtitle')}</p>
        {loading && !view ? (
          <div className="flex items-center justify-center py-10"><Loader2 className="w-5 h-5 animate-spin" /></div>
        ) : null}
        {error ? <p className="text-[12px] text-[var(--danger)] mb-2" role="alert">{error}</p> : null}
        {view ? (
          <>
            <ul className="divide-y divide-[var(--nm-hairline)]">
              {view.capabilities.map((item) => (
                <li key={item.module_class} className="flex items-center gap-3 py-2">
                  <span className="text-lg w-6 text-center" aria-hidden="true">{item.icon}</span>
                  <div className="min-w-0 flex-1">
                    <div className="flex items-center gap-2 text-[13px] font-medium text-[var(--nm-ink)]">
                      <span className="truncate">{item.name}</span>
                      <span className="text-[10px] uppercase tracking-wide text-[var(--nm-ink50)]">{t(item.builtin ? 'chat.capabilities.builtin' : 'chat.capabilities.plugin')}</span>
                      {item.locked ? <Lock className="w-3 h-3 text-[var(--nm-ink50)]" aria-label={t('chat.capabilities.locked')} /> : null}
                    </div>
                    <p className="text-[11px] text-[var(--nm-ink50)] truncate">{item.description}</p>
                    {item.context_cost_hint ? <p className="text-[10px] text-[var(--nm-ink50)]">{t('chat.capabilities.tokens', { count: item.context_cost_hint })}</p> : null}
                  </div>
                  <button
                    type="button"
                    role="switch"
                    aria-checked={item.enabled}
                    aria-label={item.name}
                    disabled={item.locked || busy === item.module_class}
                    onClick={() => void toggle(item)}
                    className={`relative h-5 w-9 rounded-full transition-colors disabled:opacity-50 ${item.enabled ? 'bg-[var(--accent-primary)]' : 'bg-[var(--nm-line)]'}`}
                  >
                    <span className={`absolute top-0.5 h-4 w-4 rounded-full bg-white transition-transform ${item.enabled ? 'translate-x-4' : 'translate-x-0.5'}`} />
                  </button>
                </li>
              ))}
            </ul>
            <p className={`mt-3 text-[11px] ${view.budget.over_budget ? 'text-[var(--warning)]' : 'text-[var(--nm-ink50)]'}`} data-testid="capability-budget">
              {t(view.budget.over_budget ? 'chat.capabilities.overBudget' : 'chat.capabilities.budget', { enabled: view.budget.enabled_tokens, baseline: view.budget.baseline_tokens, ratio: view.budget.ratio })}
            </p>
          </>
        ) : null}
      </DialogContent>
    </Dialog>
  );
}
