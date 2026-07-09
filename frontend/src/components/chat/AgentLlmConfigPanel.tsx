/**
 * AgentLlmConfigPanel — per-agent LLM config editor (modal).
 *
 * Each agent independently picks its coding-agent framework + model (agent
 * slot), its reasoning knobs, and its helper model (helper_llm slot). Any slot
 * left as "inherit" falls back to the owner's global default (Settings ›
 * Providers). Writes to the per-agent override endpoints
 * (/api/agents/{id}/llm-config); changes apply on the agent's next run.
 *
 * Modeled on AwarenessPanel's per-agent modal pattern. Provider/model options
 * are built with the shared helpers in lib/agentFramework so this offers
 * exactly the same choices the Settings default editor does.
 */
import { useCallback, useEffect, useState } from 'react';
import { Dialog, DialogContent, DialogFooter } from '@/components/ui';
import { api } from '@/lib/api';
import { cn } from '@/lib/utils';
import {
  AGENT_FRAMEWORKS,
  isCodexFramework,
  getModelsForSlot,
  prettifyModel,
  CODEX_ALLOWED_PROVIDER_SOURCES,
  RECOMMENDED_HELPER_MODEL_BY_PROTOCOL,
  type ProviderSummary,
} from '@/lib/agentFramework';
import type { AgentSlotView, AgentSlotEffective } from '@/types';

interface Props {
  agentId: string;
  isOpen: boolean;
  onClose: () => void;
  /** Fired after any successful save/reset so callers (badge) can refresh. */
  onSaved?: () => void;
}

type Draft = {
  provider_id: string;
  model: string;
  thinking: string;
  reasoning_effort: string;
  agent_framework: string; // agent slot only
};

const EMPTY_DRAFT: Draft = {
  provider_id: '',
  model: '',
  thinking: '',
  reasoning_effort: '',
  agent_framework: 'claude_code',
};

function draftFrom(eff: AgentSlotEffective | null, fallbackFramework: string): Draft {
  if (!eff) return { ...EMPTY_DRAFT, agent_framework: fallbackFramework };
  return {
    provider_id: eff.provider_id || '',
    model: eff.model || '',
    thinking: eff.thinking || '',
    reasoning_effort: eff.reasoning_effort || '',
    agent_framework: eff.agent_framework || fallbackFramework,
  };
}

export function AgentLlmConfigPanel({ agentId, isOpen, onClose, onSaved }: Props) {
  const [providers, setProviders] = useState<Record<string, ProviderSummary>>({});
  const [slots, setSlots] = useState<Record<string, AgentSlotView>>({});
  const [agentDraft, setAgentDraft] = useState<Draft>(EMPTY_DRAFT);
  const [helperDraft, setHelperDraft] = useState<Draft>(EMPTY_DRAFT);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState<string | null>(null); // slot being saved
  const [error, setError] = useState('');

  const load = useCallback(async () => {
    setLoading(true);
    setError('');
    try {
      const [cfgRes, provRes] = await Promise.all([
        api.getAgentLlmConfig(agentId),
        api.getProviders(),
      ]);
      const provMap = (provRes?.data?.providers ?? {}) as Record<string, ProviderSummary>;
      setProviders(provMap);
      const s = (cfgRes?.data?.slots ?? {}) as Record<string, AgentSlotView>;
      setSlots(s);
      const ownerFramework =
        s.agent?.owner_default?.agent_framework || 'claude_code';
      setAgentDraft(draftFrom(s.agent?.effective ?? null, ownerFramework));
      setHelperDraft(draftFrom(s.helper_llm?.effective ?? null, 'claude_code'));
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to load config');
    } finally {
      setLoading(false);
    }
  }, [agentId]);

  useEffect(() => {
    if (isOpen) void load();
  }, [isOpen, load]);

  const providerList = Object.values(providers).filter((p) => p.is_active);

  const agentProviders = providerList.filter((p) => {
    const fw = AGENT_FRAMEWORKS.find((f) => f.id === agentDraft.agent_framework);
    if (fw && p.protocol !== fw.protocol) return false;
    if (isCodexFramework(agentDraft.agent_framework)) {
      return CODEX_ALLOWED_PROVIDER_SOURCES.includes(p.source);
    }
    return true;
  });

  // Helper slot: openai/anthropic protocols, never an OAuth provider (CLI
  // credentials can't make direct API calls).
  const helperProviders = providerList.filter(
    (p) => ['openai', 'anthropic'].includes(p.protocol) && p.auth_type !== 'oauth',
  );

  const saveSlot = async (slot: 'agent' | 'helper_llm') => {
    const d = slot === 'agent' ? agentDraft : helperDraft;
    if (!d.provider_id || !d.model) {
      setError('Pick a provider and model first.');
      return;
    }
    setBusy(slot);
    setError('');
    try {
      const res = await api.setAgentLlmConfig(agentId, slot, {
        provider_id: d.provider_id,
        model: d.model,
        thinking: slot === 'agent' ? d.thinking : '',
        reasoning_effort: slot === 'agent' ? d.reasoning_effort : '',
        agent_framework: slot === 'agent' ? d.agent_framework : null,
      });
      if (!res.success) {
        setError(res.detail || 'Save failed');
        return;
      }
      await load();
      onSaved?.();
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Save failed');
    } finally {
      setBusy(null);
    }
  };

  const resetSlot = async (slot: 'agent' | 'helper_llm') => {
    setBusy(slot);
    setError('');
    try {
      await api.resetAgentLlmConfig(agentId, slot);
      await load();
      onSaved?.();
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Reset failed');
    } finally {
      setBusy(null);
    }
  };

  const selectCls =
    'w-full px-3 py-2 text-sm rounded-lg border border-[var(--border-default)] bg-[var(--bg-primary)] text-[var(--text-primary)] outline-none focus:border-[var(--accent-primary)] disabled:opacity-50';
  const labelCls = 'block text-xs text-[var(--text-tertiary)] mb-1';
  const btnPrimary =
    'px-4 py-2 text-sm font-medium rounded-lg bg-[var(--text-primary)] text-[var(--text-inverse)] hover:opacity-90 disabled:opacity-40 transition-colors';
  const btnGhost =
    'px-3 py-2 text-sm rounded-lg border border-[var(--border-default)] text-[var(--text-secondary)] hover:bg-[var(--bg-tertiary)] disabled:opacity-40 transition-colors';

  const StatusChip = ({ view }: { view?: AgentSlotView }) =>
    view?.inheriting !== false ? (
      <span className="text-xs text-[var(--text-tertiary)]">inheriting default</span>
    ) : (
      <span className="text-xs text-[var(--accent-primary)]">custom for this agent</span>
    );

  const helperRecModel =
    RECOMMENDED_HELPER_MODEL_BY_PROTOCOL[
      providers[helperDraft.provider_id]?.protocol || 'openai'
    ] || 'gpt-5.4-mini';

  return (
    <Dialog isOpen={isOpen} onClose={onClose} title="Agent model & framework" size="2xl">
      <DialogContent>
        {loading ? (
          <p className="text-sm text-[var(--text-tertiary)]">Loading…</p>
        ) : (
          <div className="space-y-6">
            <p className="text-sm text-[var(--text-tertiary)]">
              These settings apply to <span className="font-mono">{agentId}</span> only.
              Leave a slot as the inherited default to follow your global setting
              (Settings › Providers). Changes take effect on the agent's next run.
            </p>

            {/* ---- Agent slot ---- */}
            <div className="p-4 rounded-xl border border-[var(--border-subtle)] bg-[var(--bg-tertiary)]">
              <div className="flex items-center justify-between mb-3">
                <span className="text-sm font-medium text-[var(--text-primary)]">Agent (main dialogue)</span>
                <StatusChip view={slots.agent} />
              </div>

              <div className="grid grid-cols-2 gap-3">
                <div className="col-span-2">
                  <label className={labelCls}>Framework</label>
                  <select
                    className={selectCls}
                    value={agentDraft.agent_framework}
                    onChange={(e) => {
                      // Switching framework can invalidate the provider (protocol
                      // change) — clear it so the user re-picks a compatible one.
                      setAgentDraft((d) => ({
                        ...d, agent_framework: e.target.value, provider_id: '', model: '',
                      }));
                    }}
                  >
                    {AGENT_FRAMEWORKS.map((f) => (
                      <option key={f.id} value={f.id}>{f.label} — {f.desc}</option>
                    ))}
                  </select>
                </div>

                <div>
                  <label className={labelCls}>Provider</label>
                  <select
                    className={selectCls}
                    value={agentDraft.provider_id}
                    onChange={(e) => {
                      const pid = e.target.value;
                      const prov = providers[pid];
                      const models = prov
                        ? getModelsForSlot(prov, 'agent', agentDraft.agent_framework, {})
                        : [];
                      setAgentDraft((d) => ({
                        ...d, provider_id: pid, model: models[0]?.model_id || '',
                      }));
                    }}
                  >
                    <option value="">Select provider…</option>
                    {agentProviders.map((p) => (
                      <option key={p.provider_id} value={p.provider_id}>{p.name}</option>
                    ))}
                  </select>
                </div>

                <div>
                  <label className={labelCls}>Model</label>
                  <select
                    className={selectCls}
                    value={agentDraft.model}
                    disabled={!agentDraft.provider_id}
                    onChange={(e) => setAgentDraft((d) => ({ ...d, model: e.target.value }))}
                  >
                    <option value="">Select model…</option>
                    {(providers[agentDraft.provider_id]
                      ? getModelsForSlot(providers[agentDraft.provider_id], 'agent', agentDraft.agent_framework, {})
                      : []
                    ).map((m) => (
                      <option key={m.model_id} value={m.model_id}>{m.display_name}</option>
                    ))}
                  </select>
                </div>

                <div>
                  <label className={labelCls}>Thinking</label>
                  <select
                    className={selectCls}
                    value={agentDraft.thinking}
                    onChange={(e) => setAgentDraft((d) => ({ ...d, thinking: e.target.value }))}
                  >
                    <option value="">Auto (default)</option>
                    <option value="on">On</option>
                    <option value="off">Off</option>
                  </select>
                </div>

                <div>
                  <label className={labelCls}>Reasoning effort</label>
                  <select
                    className={selectCls}
                    value={agentDraft.reasoning_effort}
                    onChange={(e) => setAgentDraft((d) => ({ ...d, reasoning_effort: e.target.value }))}
                  >
                    <option value="">Auto (default)</option>
                    <option value="low">Low</option>
                    <option value="medium">Medium</option>
                    <option value="high">High</option>
                    <option value="max">Max</option>
                  </select>
                </div>
              </div>

              <div className="flex gap-2 mt-3">
                <button className={btnPrimary} disabled={busy === 'agent'} onClick={() => saveSlot('agent')}>
                  {busy === 'agent' ? 'Saving…' : 'Save for this agent'}
                </button>
                {slots.agent?.inheriting === false && (
                  <button className={btnGhost} disabled={busy === 'agent'} onClick={() => resetSlot('agent')}>
                    Reset to default
                  </button>
                )}
              </div>
            </div>

            {/* ---- Helper slot ---- */}
            <div className="p-4 rounded-xl border border-[var(--border-subtle)] bg-[var(--bg-tertiary)]">
              <div className="flex items-center justify-between mb-3">
                <span className="text-sm font-medium text-[var(--text-primary)]">
                  Helper LLM (background tasks)
                </span>
                <StatusChip view={slots.helper_llm} />
              </div>

              <div className="grid grid-cols-2 gap-3">
                <div>
                  <label className={labelCls}>Provider</label>
                  <select
                    className={selectCls}
                    value={helperDraft.provider_id}
                    onChange={(e) => {
                      const pid = e.target.value;
                      const prov = providers[pid];
                      const models = prov ? getModelsForSlot(prov, 'helper_llm', null, {}) : [];
                      setHelperDraft((d) => ({
                        ...d, provider_id: pid, model: models[0]?.model_id || '',
                      }));
                    }}
                  >
                    <option value="">Select provider…</option>
                    {helperProviders.map((p) => (
                      <option key={p.provider_id} value={p.provider_id}>{p.name}</option>
                    ))}
                  </select>
                </div>

                <div>
                  <label className={labelCls}>Model</label>
                  <select
                    className={selectCls}
                    value={helperDraft.model}
                    disabled={!helperDraft.provider_id}
                    onChange={(e) => setHelperDraft((d) => ({ ...d, model: e.target.value }))}
                  >
                    <option value="">Select model…</option>
                    {(providers[helperDraft.provider_id]
                      ? getModelsForSlot(providers[helperDraft.provider_id], 'helper_llm', null, {})
                      : []
                    ).map((m) => (
                      <option key={m.model_id} value={m.model_id}>{m.display_name}</option>
                    ))}
                  </select>
                </div>
              </div>
              <p className="text-xs text-[var(--text-tertiary)] mt-2">
                Recommended: {prettifyModel(helperRecModel)} — small/fast model for
                summaries, dedup, memory. OAuth (CLI sign-in) providers can't be used here.
              </p>

              <div className="flex gap-2 mt-3">
                <button className={btnPrimary} disabled={busy === 'helper_llm'} onClick={() => saveSlot('helper_llm')}>
                  {busy === 'helper_llm' ? 'Saving…' : 'Save for this agent'}
                </button>
                {slots.helper_llm?.inheriting === false && (
                  <button className={btnGhost} disabled={busy === 'helper_llm'} onClick={() => resetSlot('helper_llm')}>
                    Reset to default
                  </button>
                )}
              </div>
            </div>

            {error && <p className="text-sm text-[var(--color-error)]">{error}</p>}
          </div>
        )}
      </DialogContent>
      <DialogFooter>
        <button
          className={cn(btnGhost)}
          onClick={onClose}
        >
          Close
        </button>
      </DialogFooter>
    </Dialog>
  );
}
