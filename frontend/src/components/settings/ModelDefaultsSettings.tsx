/**
 * ModelDefaultsSettings — the GLOBAL DEFAULT model config (Settings › Model
 * Defaults).
 *
 * The provider + model + coding-agent framework every agent INHERITS by
 * default. Per-agent overrides live in the chat page (the model chip + the
 * header ⚙ → AgentLlmConfigPanel). This panel writes the user-level slots via
 * the unchanged endpoints:
 *   - PUT  /api/providers/slots/{agent|helper_llm}   (setProviderSlot)
 *   - POST /api/providers/agent-framework            (setAgentFramework)
 *
 * Extracted out of ProviderSettings' old "Section ③" so LLM Providers is purely
 * the credential wallet. Option-building is shared via lib/agentFramework so the
 * choices match the per-agent panel + the provider dropdowns.
 */
import { useCallback, useEffect, useState } from 'react';
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

type AgentDraft = {
  provider_id: string;
  model: string;
  thinking: string;
  reasoning_effort: string;
};
type HelperDraft = { provider_id: string; model: string };

const EMPTY_AGENT: AgentDraft = { provider_id: '', model: '', thinking: '', reasoning_effort: '' };
const EMPTY_HELPER: HelperDraft = { provider_id: '', model: '' };

interface SlotCfg {
  provider_id?: string;
  model?: string;
  thinking?: string;
  reasoning_effort?: string;
}

export function ModelDefaultsSettings() {
  const [providers, setProviders] = useState<Record<string, ProviderSummary>>({});
  const [framework, setFramework] = useState('claude_code');
  const [probe, setProbe] = useState<{ ok: boolean; detail: string } | null>(null);
  const [install, setInstall] = useState<{ action: string; reason: string } | null>(null);
  const [frameworkSaving, setFrameworkSaving] = useState(false);
  const [agentDraft, setAgentDraft] = useState<AgentDraft>(EMPTY_AGENT);
  const [helperDraft, setHelperDraft] = useState<HelperDraft>(EMPTY_HELPER);
  const [agentInitial, setAgentInitial] = useState<AgentDraft>(EMPTY_AGENT);
  const [helperInitial, setHelperInitial] = useState<HelperDraft>(EMPTY_HELPER);
  const [loading, setLoading] = useState(true);
  const [applying, setApplying] = useState(false);
  const [error, setError] = useState('');
  const [saved, setSaved] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    setError('');
    try {
      const [provRes, fwRes] = await Promise.all([
        api.getProviders(),
        api.getAgentFramework(),
      ]);
      const provMap = (provRes?.data?.providers ?? {}) as Record<string, ProviderSummary>;
      setProviders(provMap);
      const slots = (provRes?.data?.slots ?? {}) as Record<string, { config?: SlotCfg | null }>;
      const a = slots.agent?.config ?? null;
      const h = slots.helper_llm?.config ?? null;
      const agent: AgentDraft = {
        provider_id: a?.provider_id || '',
        model: a?.model || '',
        thinking: a?.thinking || '',
        reasoning_effort: a?.reasoning_effort || '',
      };
      const helper: HelperDraft = { provider_id: h?.provider_id || '', model: h?.model || '' };
      setAgentDraft(agent);
      setHelperDraft(helper);
      setAgentInitial(agent);
      setHelperInitial(helper);
      if (fwRes?.success) {
        setFramework(fwRes.data.framework);
        setProbe(fwRes.data.probe);
      }
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to load');
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { void load(); }, [load]);

  const providerList = Object.values(providers).filter((p) => p.is_active);
  const hasProviders = providerList.length > 0;

  const agentProviders = providerList.filter((p) => {
    const fw = AGENT_FRAMEWORKS.find((f) => f.id === framework);
    if (fw && p.protocol !== fw.protocol) return false;
    if (isCodexFramework(framework)) return CODEX_ALLOWED_PROVIDER_SOURCES.includes(p.source);
    return true;
  });
  const helperProviders = providerList.filter(
    (p) => ['openai', 'anthropic'].includes(p.protocol) && p.auth_type !== 'oauth',
  );

  const sameAgent = (a: AgentDraft, b: AgentDraft) =>
    a.provider_id === b.provider_id && a.model === b.model &&
    a.thinking === b.thinking && a.reasoning_effort === b.reasoning_effort;
  const sameHelper = (a: HelperDraft, b: HelperDraft) =>
    a.provider_id === b.provider_id && a.model === b.model;

  const agentChanged = !sameAgent(agentDraft, agentInitial);
  const helperChanged = !sameHelper(helperDraft, helperInitial);
  const isDirty = agentChanged || helperChanged;

  // Framework switch persists immediately (it may auto-install codex + re-probe
  // auth); switching protocol clears the agent provider/model so the user picks
  // a compatible one.
  const onFrameworkChange = async (next: string) => {
    setFramework(next);
    setAgentDraft((d) => ({ ...d, provider_id: '', model: '' }));
    setFrameworkSaving(true);
    setError('');
    setInstall(null);
    try {
      const resp = await api.setAgentFramework(next);
      if (resp.success) {
        setProbe(resp.data.probe);
        setInstall(resp.data.install);
      }
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to switch framework');
    } finally {
      setFrameworkSaving(false);
    }
  };

  const apply = async () => {
    if (!isDirty || applying) return;
    if (agentChanged && (!agentDraft.provider_id || !agentDraft.model)) {
      setError('Pick a provider and model for the agent slot.');
      return;
    }
    if (helperChanged && (!helperDraft.provider_id || !helperDraft.model)) {
      setError('Pick a provider and model for the helper slot.');
      return;
    }
    setApplying(true);
    setError('');
    try {
      if (agentChanged) {
        const r = await api.setProviderSlot('agent', {
          provider_id: agentDraft.provider_id,
          model: agentDraft.model,
          thinking: agentDraft.thinking,
          reasoning_effort: agentDraft.reasoning_effort,
        });
        if (!r.success) { setError(r.detail || 'Save failed'); return; }
      }
      if (helperChanged) {
        const r = await api.setProviderSlot('helper_llm', {
          provider_id: helperDraft.provider_id,
          model: helperDraft.model,
        });
        if (!r.success) { setError(r.detail || 'Save failed'); return; }
      }
      await load();
      setSaved(true);
      setTimeout(() => setSaved(false), 2500);
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Save failed');
    } finally {
      setApplying(false);
    }
  };

  const selectCls =
    'w-full px-3 py-2 text-sm rounded-lg border border-[var(--border-default)] bg-[var(--bg-primary)] text-[var(--text-primary)] outline-none focus:border-[var(--accent-primary)] disabled:opacity-50';
  const labelCls = 'block text-xs text-[var(--text-tertiary)] mb-1';
  const btnPrimary =
    'px-5 py-2.5 text-sm font-medium rounded-lg bg-[var(--text-primary)] text-[var(--text-inverse)] hover:opacity-90 disabled:opacity-40 transition-colors';

  const helperRecModel =
    RECOMMENDED_HELPER_MODEL_BY_PROTOCOL[providers[helperDraft.provider_id]?.protocol || 'openai'] || 'gpt-5.4-mini';

  if (loading) {
    return <p className="text-sm text-[var(--text-tertiary)]">Loading…</p>;
  }

  if (!hasProviders) {
    return (
      <p className="text-sm text-[var(--text-tertiary)]">
        No providers yet — add one under <span className="font-medium">LLM Providers</span> first,
        then set the default model here.
      </p>
    );
  }

  return (
    <div className="space-y-6 max-w-2xl">
      <p className="text-sm text-[var(--text-tertiary)]">
        The framework + model every agent inherits by default. To give one agent
        its own model, change it in that agent's chat (the model chip next to the
        composer).
      </p>

      {/* ---- Agent slot ---- */}
      <div className="p-4 rounded-xl border border-[var(--border-subtle)] bg-[var(--bg-tertiary)]">
        <div className="text-sm font-medium text-[var(--text-primary)] mb-3">Agent (main dialogue)</div>
        <div className="grid grid-cols-2 gap-3">
          <div className="col-span-2">
            <label className={labelCls}>
              Framework
              {probe && (
                <span
                  className={cn('ml-2 text-xs', probe.ok ? 'text-[var(--color-success)]' : 'text-[var(--color-error)]')}
                  title={probe.detail}
                >
                  {probe.ok ? '✓ auth ready' : '✗ auth missing'}
                </span>
              )}
            </label>
            <select
              className={selectCls}
              value={framework}
              disabled={frameworkSaving}
              onChange={(e) => onFrameworkChange(e.target.value)}
            >
              {AGENT_FRAMEWORKS.map((f) => (
                <option key={f.id} value={f.id}>{f.label} — {f.desc}</option>
              ))}
            </select>
            {frameworkSaving && isCodexFramework(framework) && (
              <div className="text-xs text-[var(--text-tertiary)] mt-1 italic">Verifying Codex CLI…</div>
            )}
            {install && install.action === 'install_failed' && (
              <div className="text-xs text-[var(--color-error)] mt-1">Codex binary unavailable: {install.reason}</div>
            )}
            {probe && !probe.ok && !(install && install.action === 'install_failed') && (
              <div className="text-xs text-[var(--text-tertiary)] mt-1">{probe.detail}</div>
            )}
          </div>

          <div>
            <label className={labelCls}>Provider</label>
            <select
              className={selectCls}
              value={agentDraft.provider_id}
              onChange={(e) => {
                const pid = e.target.value;
                const prov = providers[pid];
                const models = prov ? getModelsForSlot(prov, 'agent', framework, {}) : [];
                setAgentDraft((d) => ({ ...d, provider_id: pid, model: models[0]?.model_id || '' }));
              }}
            >
              <option value="">Select provider…</option>
              {agentProviders.map((p) => (<option key={p.provider_id} value={p.provider_id}>{p.name}</option>))}
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
                ? getModelsForSlot(providers[agentDraft.provider_id], 'agent', framework, {})
                : []
              ).map((m) => (<option key={m.model_id} value={m.model_id}>{m.display_name}</option>))}
            </select>
          </div>

          <div>
            <label className={labelCls}>Thinking</label>
            <select className={selectCls} value={agentDraft.thinking}
              onChange={(e) => setAgentDraft((d) => ({ ...d, thinking: e.target.value }))}>
              <option value="">Auto (default)</option>
              <option value="on">On</option>
              <option value="off">Off</option>
            </select>
          </div>

          <div>
            <label className={labelCls}>Reasoning effort</label>
            <select className={selectCls} value={agentDraft.reasoning_effort}
              onChange={(e) => setAgentDraft((d) => ({ ...d, reasoning_effort: e.target.value }))}>
              <option value="">Auto (default)</option>
              <option value="low">Low</option>
              <option value="medium">Medium</option>
              <option value="high">High</option>
              <option value="max">Max</option>
            </select>
          </div>
        </div>
      </div>

      {/* ---- Helper slot ---- */}
      <div className="p-4 rounded-xl border border-[var(--border-subtle)] bg-[var(--bg-tertiary)]">
        <div className="text-sm font-medium text-[var(--text-primary)] mb-3">Helper LLM (background tasks)</div>
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
                setHelperDraft({ provider_id: pid, model: models[0]?.model_id || '' });
              }}
            >
              <option value="">Select provider…</option>
              {helperProviders.map((p) => (<option key={p.provider_id} value={p.provider_id}>{p.name}</option>))}
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
              ).map((m) => (<option key={m.model_id} value={m.model_id}>{m.display_name}</option>))}
            </select>
          </div>
        </div>
        <p className="text-xs text-[var(--text-tertiary)] mt-2">
          Recommended: {prettifyModel(helperRecModel)} — small/fast model for summaries,
          dedup, memory. OAuth (CLI sign-in) providers can't be used here.
        </p>
      </div>

      {error && <p className="text-sm text-[var(--color-error)]">{error}</p>}

      <div className="flex items-center gap-3">
        <button className={btnPrimary} disabled={!isDirty || applying} onClick={apply}>
          {applying ? 'Saving…' : 'Save defaults'}
        </button>
        {saved && !isDirty && (
          <span className="text-sm text-[var(--color-success)]">✓ Saved</span>
        )}
      </div>
    </div>
  );
}
