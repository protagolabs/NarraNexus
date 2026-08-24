/**
 * @file_name: CreateAgentPage.tsx
 * @author:
 * @date: 2026-08-20
 * @description: Chat UI v4 — agent creation as a modal (the sidebar New
 * menu's "Create agent" entry, replacing the old one-click blank agent).
 * Routed at /app/agents/new; renders as the shared Dialog primitive (dimmed
 * backdrop, centered card) rather than a full-bleed page — Cancel/the
 * backdrop/Escape/the header X all navigate back to /app/chat. Three boxes
 * inside: Awareness (name + description), Engine (framework + model config —
 * Framework/Provider/Model/Helper-LLM-provider/Helper-LLM-model all render
 * through the local IconSelect (a Popover-based dropdown, since a native
 * <select>/<option> cannot render an icon in any browser) so each shows a
 * real vendor mark: Claude/OpenAI/NexusPower for Framework, protocol-based
 * Claude/OpenAI for Provider, and a model-id-prefix match — Claude/OpenAI/
 * Gemini/GLM/Kimi/Qwen/MiniMax/DeepSeek — for Model, all from
 * ModelBrandIcons.tsx + lib/modelBrandIcons.ts), Channel (click a real brand
 * icon — Discord/WeChat/Slack/Telegram/Lark/Home
 * Assistant — to inline-expand that channel's bind form; accordion, not a
 * checklist: only one is open at a time, opening another closes the last.
 * Clicking Connect validates the open form's required fields, marks it lit
 * (green dot) and closes it — typing alone does NOT light it up), Config
 * (skills + a disabled MCP placeholder).
 *
 * The backend has no single endpoint that accepts all of this atomically —
 * POST /agents only takes name/description/team_id. So Create sequences
 * existing per-agent endpoints client-side: create the agent, then fire the
 * LLM-config PUT, the awareness PUT, one marketplace-install call per
 * selected skill, and one bind call per channel the user actually clicked
 * Connect on (connectedChannels). Step 1 failing aborts (nothing is
 * created). Anything after that failing does NOT roll back or delete the
 * agent — the user lands in its chat with a non-blocking notice, since every
 * one of those settings has its own retry surface already
 * (AgentLlmConfigPanel, AwarenessPanel, IMChannelsSection, SkillsPanel).
 *
 * MCP has no agent-independent catalog to pick from — every MCP endpoint
 * (backend/routes/agents/mcps.py) requires an existing agent_id, so that row
 * stays a disabled placeholder pointing at post-creation setup. Channel is
 * different: 5 of its 6 bind flows are plain credential forms (bot token,
 * app secret, base URL — see ChannelBrandIcons.tsx / awareness.* i18n reuse
 * below) that can be filled in now and submitted once agent_id exists, same
 * as Skills. Only WeChat can't — its bind is a live QR-scan session that
 * needs a real agent_id just to start, so its expand panel is a plain
 * "available after creation" note instead of a form (and never gets Connect
 * / never lights up).
 */
import { useCallback, useEffect, useMemo, useState, type ReactNode } from 'react';
import { useTranslation } from 'react-i18next';
import { useNavigate } from 'react-router-dom';
import { ChevronDown, Key, Loader2, Search, Terminal } from 'lucide-react';
import { Button, Dialog, DialogContent, DialogFooter, useNotice } from '@/components/ui';
import { OneKeyOnboard } from '@/components/settings/OneKeyOnboard';
import { CustomEndpointForm } from '@/components/providers/CustomEndpointForm';
import { CliSignInPanel } from '@/components/providers/CliSignInPanel';
import { Popover, PopoverContent, PopoverTrigger } from '@/components/ui/popover';
import {
  DiscordBrandIcon,
  HomeAssistantBrandIcon,
  LarkBrandIcon,
  NarraMessengerBrandIcon,
  SlackBrandIcon,
  TelegramBrandIcon,
  WeChatBrandIcon,
} from '@/components/icons/ChannelBrandIcons';
import { useConfigStore } from '@/stores';
import { useCreateAgent } from '@/hooks';
import { useMarketplaceSearch } from '@/hooks/useSkillMarketplace';
import { api } from '@/lib/api';
import { cn } from '@/lib/utils';
import { getModelBrandIcon, getProtocolBrandIcon } from '@/lib/modelBrandIcons';
import {
  AGENT_FRAMEWORKS,
  getModelsForSlot,
  defaultHelperModel,
  cloudNetmindOnly,
  isSlotBindableSource,
  deriveFrameworkFromProvider,
  type ProviderSummary,
} from '@/lib/agentFramework';
import type { MarketplaceSkillItem } from '@/types/skills';

type AgentDraft = {
  provider_id: string;
  model: string;
  thinking: string;
  reasoning_effort: string;
};
type HelperDraft = { provider_id: string; model: string };

const EMPTY_AGENT: AgentDraft = { provider_id: '', model: '', thinking: '', reasoning_effort: '' };
const EMPTY_HELPER: HelperDraft = { provider_id: '', model: '' };

// Same 7 channels as IM_CHANNELS in components/awareness/IMChannelsSection.tsx
// (+ Home Assistant, which lives outside that registry), all real brand
// marks (see ChannelBrandIcons.tsx — Lark's is a raster pulled from its own
// favicon since no vector of it exists anywhere; NarraMessenger's is this
// app's own logo, it being a first-party product). Every channel's bind
// call (QR scan / OAuth / bot-token / bind-link save) strictly requires an
// existing agent_id, so nothing here fires an API call until Create — see
// handleCreate.
type ChannelKey = 'lark' | 'slack' | 'telegram' | 'wechat' | 'discord' | 'home_assistant' | 'narramessenger';
type ChannelIconComponent = (props: { className?: string }) => ReactNode;
const CHANNEL_ICONS: Array<{ key: ChannelKey; label: string; Icon: ChannelIconComponent }> = [
  { key: 'lark', label: 'Lark / Feishu', Icon: LarkBrandIcon },
  { key: 'slack', label: 'Slack', Icon: SlackBrandIcon },
  { key: 'telegram', label: 'Telegram', Icon: TelegramBrandIcon },
  { key: 'wechat', label: 'WeChat', Icon: WeChatBrandIcon },
  { key: 'discord', label: 'Discord', Icon: DiscordBrandIcon },
  { key: 'home_assistant', label: 'Home Assistant', Icon: HomeAssistantBrandIcon },
  { key: 'narramessenger', label: 'NarraMessenger', Icon: NarraMessengerBrandIcon },
];

// Wizard: step 1 picks/adds a provider, step 2 fills in the rest of the
// agent's details (Awareness/Engine/Channel/Config boxes).
type WizardStep = 'provider' | 'details';

type DiscordDraft = { botToken: string; ownerUserId: string };
type TelegramDraft = { botToken: string; ownerUsername: string };
type SlackDraft = { botToken: string; appToken: string; ownerEmail: string };
type LarkDraft = { appId: string; appSecret: string; brand: 'feishu' | 'lark'; ownerEmail: string };
type HaDraft = { baseUrl: string; token: string; verifyTls: boolean };
type NarraMessengerDraft = { bindCommand: string };

const EMPTY_DISCORD: DiscordDraft = { botToken: '', ownerUserId: '' };
const EMPTY_TELEGRAM: TelegramDraft = { botToken: '', ownerUsername: '' };
const EMPTY_SLACK: SlackDraft = { botToken: '', appToken: '', ownerEmail: '' };
const EMPTY_LARK: LarkDraft = { appId: '', appSecret: '', brand: 'feishu', ownerEmail: '' };
const EMPTY_NARRAMESSENGER: NarraMessengerDraft = { bindCommand: '' };
const EMPTY_HA: HaDraft = { baseUrl: '', token: '', verifyTls: true };

interface SlotCfg {
  provider_id?: string;
  model?: string;
  thinking?: string;
  reasoning_effort?: string;
}

const fieldLabel =
  'font-mono text-[11px] uppercase tracking-[0.1em] text-[var(--nm-ink50)]';
const inputCls =
  'w-full rounded-[var(--radius-sm)] border border-[var(--border-subtle)] bg-[var(--nm-card)] px-3 text-[13px] text-[var(--nm-ink)] placeholder:text-[var(--nm-ink30)] focus:outline-none focus:border-[var(--border-strong)]';
const selectCls =
  'w-full h-9 px-3 text-[13px] rounded-[var(--radius-sm)] border border-[var(--border-subtle)] bg-[var(--nm-card)] text-[var(--nm-ink)] outline-none focus:border-[var(--border-strong)] disabled:opacity-50';
const boxCls =
  // Sidebar's own tone (var(--nm-paper)) on the boxes — the modal shell
  // itself is pure white (Dialog's `bg` override below).
  'flex flex-col gap-4 rounded-[var(--radius-md)] border border-[var(--nm-hairline)] bg-[var(--nm-paper)] p-5';
const boxTitleCls = 'text-sm font-semibold text-[var(--nm-ink)]';

interface IconSelectOption {
  value: string;
  label: string;
  Icon?: ChannelIconComponent | null;
}

/** Native <select> can't render an icon inside its <option> list in any
 *  browser — this is the Popover-based substitute used everywhere Framework
 *  / Provider / Model need a brand icon next to the label, both on the
 *  closed trigger and in the open list. Same interaction shape as the
 *  Skills picker below (button trigger + PopoverContent list). */
function IconSelect({
  value,
  options,
  onChange,
  placeholder,
  disabled,
}: {
  value: string;
  options: IconSelectOption[];
  onChange: (value: string) => void;
  placeholder: string;
  disabled?: boolean;
}) {
  const [open, setOpen] = useState(false);
  const selected = options.find((o) => o.value === value);
  return (
    <Popover open={open} onOpenChange={(v) => !disabled && setOpen(v)}>
      <PopoverTrigger asChild>
        <button
          type="button"
          disabled={disabled}
          className={cn(selectCls, 'flex items-center justify-between gap-2 text-left')}
        >
          <span className="flex min-w-0 items-center gap-2">
            {selected?.Icon && <selected.Icon className="h-4 w-4 shrink-0" />}
            <span className="truncate">{selected ? selected.label : placeholder}</span>
          </span>
          <ChevronDown className="h-3.5 w-3.5 shrink-0 text-[var(--nm-ink30)]" />
        </button>
      </PopoverTrigger>
      <PopoverContent
        align="start"
        // Popover's own default is z-[100] — this page renders inside the
        // shared Dialog primitive, whose content wrapper is z-[1001]
        // (see components/ui/Dialog.tsx), so without this override every
        // popover here painted BEHIND the modal card: technically open,
        // invisible and unclickable. Same fix applied to the Skills popover
        // below.
        className="z-[1100] max-h-[280px] overflow-y-auto p-1"
        style={{ width: 'var(--radix-popover-trigger-width)' }}
      >
        {options.map((o) => (
          <button
            key={o.value}
            type="button"
            onClick={() => { onChange(o.value); setOpen(false); }}
            className={cn(
              'flex w-full items-center gap-2 rounded-[var(--radius-sm)] px-2.5 py-2 text-left text-[13px] transition-colors hover:bg-[var(--nm-card)]',
              o.value === value ? 'text-[var(--nm-ink)]' : 'text-[var(--nm-ink70)]',
            )}
          >
            {o.Icon && <o.Icon className="h-4 w-4 shrink-0" />}
            <span className="truncate">{o.label}</span>
          </button>
        ))}
      </PopoverContent>
    </Popover>
  );
}

export default function CreateAgentPage() {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const role = useConfigStore((s) => s.role);
  const netmindOnly = cloudNetmindOnly(role);
  const { createAgent } = useCreateAgent();
  const { notifyError, dialog } = useNotice();

  // ---- Awareness box ----
  const [name, setName] = useState('');
  const [description, setDescription] = useState('');

  // ---- Engine box ----
  const [providers, setProviders] = useState<Record<string, ProviderSummary>>({});
  const [framework, setFramework] = useState('claude_code');
  const [frameworkInitial, setFrameworkInitial] = useState('claude_code');
  const [agentDraft, setAgentDraft] = useState<AgentDraft>(EMPTY_AGENT);
  const [helperDraft, setHelperDraft] = useState<HelperDraft>(EMPTY_HELPER);
  // Snapshot of the prefilled global default — Create only writes a per-agent
  // override for a slot the user actually changed from this, so an untouched
  // Engine box still inherits the global default going forward instead of
  // pinning today's value as a standing override.
  const [agentInitial, setAgentInitial] = useState<AgentDraft>(EMPTY_AGENT);
  const [helperInitial, setHelperInitial] = useState<HelperDraft>(EMPTY_HELPER);
  const [loadingEngine, setLoadingEngine] = useState(true);

  // ---- Channel box ----
  // Accordion, not a checklist: only one channel's form is open at a time —
  // clicking another icon switches to it instead of stacking panels.
  const [expandedChannel, setExpandedChannel] = useState<ChannelKey | null>(null);
  // A channel only "lights up" once its Connect button is clicked with valid
  // fields — typing alone doesn't count, unlike Skills' pick-and-go list.
  const [connectedChannels, setConnectedChannels] = useState<Set<ChannelKey>>(new Set());
  const [channelError, setChannelError] = useState('');
  const [discordDraft, setDiscordDraft] = useState<DiscordDraft>(EMPTY_DISCORD);
  const [telegramDraft, setTelegramDraft] = useState<TelegramDraft>(EMPTY_TELEGRAM);
  const [slackDraft, setSlackDraft] = useState<SlackDraft>(EMPTY_SLACK);
  const [larkDraft, setLarkDraft] = useState<LarkDraft>(EMPTY_LARK);
  const [haDraft, setHaDraft] = useState<HaDraft>(EMPTY_HA);
  const [narramessengerDraft, setNarramessengerDraft] = useState<NarraMessengerDraft>(EMPTY_NARRAMESSENGER);

  // ---- Config box ----
  const [skillQuery, setSkillQuery] = useState('');
  const [skillInput, setSkillInput] = useState('');
  const [selectedSkills, setSelectedSkills] = useState<Map<string, MarketplaceSkillItem>>(new Map());
  const [skillsOpen, setSkillsOpen] = useState(false);

  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');

  const [step, setStep] = useState<WizardStep>('provider');
  const [addMethod, setAddMethod] = useState<'apikey' | 'cli' | null>(null);
  const [apiKeySubTab, setApiKeySubTab] = useState<'quick' | 'custom'>('quick');

  useEffect(() => {
    const handle = setTimeout(() => setSkillQuery(skillInput.trim()), 300);
    return () => clearTimeout(handle);
  }, [skillInput]);

  const { data: skillResults, isLoading: skillsLoading } = useMarketplaceSearch(skillQuery, skillsOpen);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      setLoadingEngine(true);
      try {
        const [provRes, fwRes] = await Promise.all([api.getProviders(), api.getAgentFramework()]);
        if (cancelled) return;
        setProviders((provRes?.data?.providers ?? {}) as Record<string, ProviderSummary>);
        if (fwRes?.success) {
          setFramework(fwRes.data.framework);
          setFrameworkInitial(fwRes.data.framework);
        }
        // Default-select provider/model the same way Settings › Model Defaults
        // does, so the page doesn't hand the user two empty dropdowns when
        // they already have a global default configured.
        const slots = (provRes?.data?.slots ?? {}) as Record<string, { config?: SlotCfg | null }>;
        const a = slots.agent?.config;
        if (a?.provider_id && a?.model) {
          const d: AgentDraft = {
            provider_id: a.provider_id,
            model: a.model,
            thinking: a.thinking || '',
            reasoning_effort: a.reasoning_effort || '',
          };
          setAgentDraft(d);
          setAgentInitial(d);
        }
        const h = slots.helper_llm?.config;
        if (h?.provider_id && h?.model) {
          const d: HelperDraft = { provider_id: h.provider_id, model: h.model };
          setHelperDraft(d);
          setHelperInitial(d);
        }
      } finally {
        if (!cancelled) setLoadingEngine(false);
      }
    })();
    return () => { cancelled = true; };
  }, []);

  const providerList = Object.values(providers).filter((p) => p.is_active);
  const bindableProviders = providerList.filter((p) => !netmindOnly || isSlotBindableSource(p.source));
  const helperProviders = providerList.filter(
    (p) => (!netmindOnly || isSlotBindableSource(p.source)) && ['openai', 'anthropic'].includes(p.protocol),
  );

  const sameAgentDraft = (a: AgentDraft, b: AgentDraft) =>
    a.provider_id === b.provider_id && a.model === b.model &&
    a.thinking === b.thinking && a.reasoning_effort === b.reasoning_effort;
  const sameHelperDraft = (a: HelperDraft, b: HelperDraft) =>
    a.provider_id === b.provider_id && a.model === b.model;

  const applyProviderSelection = (pid: string, prov: ProviderSummary) => {
    const fw = deriveFrameworkFromProvider(prov);
    const models = getModelsForSlot(prov, 'agent', fw, {});
    setFramework(fw);
    setAgentDraft((d) => ({ ...d, provider_id: pid, model: models[0]?.model_id || '' }));
  };

  const selectProvider = (pid: string) => {
    if (pid === agentDraft.provider_id) return;
    const prov = providers[pid];
    if (prov) applyProviderSelection(pid, prov);
  };

  const handleProviderAdded = async () => {
    try {
      const prevIds = new Set(Object.keys(providers));
      const res = await api.getProviders();
      const nextProviders = (res?.data?.providers ?? {}) as Record<string, ProviderSummary>;
      setProviders(nextProviders);
      const addedId = Object.keys(nextProviders).find((id) => !prevIds.has(id));
      if (addedId && nextProviders[addedId]) applyProviderSelection(addedId, nextProviders[addedId]);
    } catch {
      await notifyError(t('pages.settings.modelDefaults.loadFailed'));
    } finally {
      setAddMethod(null);
    }
  };

  const toggleSkill = (item: MarketplaceSkillItem) => {
    setSelectedSkills((prev) => {
      const next = new Map(prev);
      if (next.has(item.skill_id)) next.delete(item.skill_id);
      else next.set(item.skill_id, item);
      return next;
    });
  };

  const toggleChannel = (key: ChannelKey) => {
    setChannelError('');
    setExpandedChannel((prev) => (prev === key ? null : key));
  };

  // Whether this channel's draft has its required fields — the same gate
  // both the Connect button (does clicking it succeed?) and handleCreate
  // (does Create actually fire this channel's bind call?) use, so the two
  // can never disagree about what counts as "enough to connect".
  const channelHasRequiredFields = (key: ChannelKey): boolean => {
    switch (key) {
      case 'discord': return !!discordDraft.botToken.trim();
      case 'telegram': return !!telegramDraft.botToken.trim();
      case 'slack': return !!(slackDraft.botToken.trim() && slackDraft.appToken.trim());
      case 'lark': return !!(larkDraft.appId.trim() && larkDraft.appSecret.trim());
      case 'home_assistant': return !!(haDraft.baseUrl.trim() && haDraft.token.trim());
      case 'narramessenger': return !!narramessengerDraft.bindCommand.trim();
      case 'wechat': return false;
    }
  };

  // Connect only marks the channel lit + collapses its panel — it does NOT
  // call any bind API (every channel's bind requires an agent_id this page
  // doesn't have yet). The real bind call fires from handleCreate, gated on
  // `connectedChannels.has(key)` exactly like this local confirmation.
  const connectChannel = (key: ChannelKey) => {
    if (!channelHasRequiredFields(key)) {
      setChannelError(t('pages.createAgent.channelConnectMissing'));
      return;
    }
    setChannelError('');
    setConnectedChannels((prev) => new Set(prev).add(key));
    setExpandedChannel(null);
  };

  const canCreate = name.trim().length > 0 && !busy;

  const handleCreate = useCallback(async () => {
    if (!name.trim()) {
      setError(t('pages.createAgent.nameRequired'));
      return;
    }
    setError('');
    setBusy(true);
    try {
      const trimmedName = name.trim();
      const trimmedDesc = description.trim();
      const agentId = await createAgent({
        name: trimmedName,
        description: trimmedDesc || undefined,
      });
      if (!agentId) throw new Error(t('pages.createAgent.createFailed'));

      const failures: string[] = [];

      // Awareness seed — always runs, name is always present.
      const seed = trimmedDesc ? `${trimmedName}\n\n${trimmedDesc}` : trimmedName;
      try {
        await api.updateAwareness(agentId, seed);
      } catch {
        failures.push(t('pages.createAgent.awarenessSectionTitle'));
      }

      // Engine — only write a slot the user actually changed from the
      // prefilled global default; an untouched slot keeps inheriting the
      // owner default going forward instead of pinning today's value.
      const agentChanged = framework !== frameworkInitial || !sameAgentDraft(agentDraft, agentInitial);
      const helperChanged = !sameHelperDraft(helperDraft, helperInitial);
      if (agentChanged && agentDraft.provider_id && agentDraft.model) {
        try {
          const r = await api.setAgentLlmConfig(agentId, 'agent', {
            provider_id: agentDraft.provider_id,
            model: agentDraft.model,
            thinking: agentDraft.thinking,
            reasoning_effort: agentDraft.reasoning_effort,
            agent_framework: framework,
          });
          if (!r.success) failures.push(t('pages.createAgent.engineSectionTitle'));
        } catch {
          failures.push(t('pages.createAgent.engineSectionTitle'));
        }
      }
      if (helperChanged && helperDraft.provider_id && helperDraft.model) {
        try {
          const r = await api.setAgentLlmConfig(agentId, 'helper_llm', {
            provider_id: helperDraft.provider_id,
            model: helperDraft.model,
          });
          if (!r.success) failures.push(t('pages.createAgent.engineSectionTitle'));
        } catch {
          failures.push(t('pages.createAgent.engineSectionTitle'));
        }
      }

      // Channel — only channels the user explicitly clicked Connect on
      // (connectedChannels) get bound; a filled-in-but-not-connected draft is
      // left untouched, same as an unconfirmed Skill never gets installed.
      if (connectedChannels.has('discord') && discordDraft.botToken.trim()) {
        try {
          const r = await api.bindDiscordBot(agentId, discordDraft.botToken.trim(), discordDraft.ownerUserId.trim());
          if (!r.success) failures.push('Discord');
        } catch {
          failures.push('Discord');
        }
      }
      if (connectedChannels.has('telegram') && telegramDraft.botToken.trim()) {
        try {
          const r = await api.bindTelegramBot(agentId, telegramDraft.botToken.trim(), telegramDraft.ownerUsername.trim());
          if (!r.success) failures.push('Telegram');
        } catch {
          failures.push('Telegram');
        }
      }
      if (connectedChannels.has('slack') && slackDraft.botToken.trim() && slackDraft.appToken.trim()) {
        try {
          const r = await api.bindSlackBot(agentId, slackDraft.botToken.trim(), slackDraft.appToken.trim(), slackDraft.ownerEmail.trim());
          if (!r.success) failures.push('Slack');
        } catch {
          failures.push('Slack');
        }
      }
      if (connectedChannels.has('lark') && larkDraft.appId.trim() && larkDraft.appSecret.trim()) {
        try {
          const r = await api.bindLarkBot(agentId, larkDraft.appId.trim(), larkDraft.appSecret.trim(), larkDraft.brand, larkDraft.ownerEmail.trim());
          if (!r.success) failures.push('Lark / Feishu');
        } catch {
          failures.push('Lark / Feishu');
        }
      }
      if (connectedChannels.has('home_assistant') && haDraft.baseUrl.trim() && haDraft.token.trim()) {
        try {
          const r = await api.saveHABinding(agentId, haDraft.baseUrl.trim(), haDraft.token.trim(), haDraft.verifyTls);
          if (!r.ok) failures.push('Home Assistant');
        } catch {
          failures.push('Home Assistant');
        }
      }
      if (connectedChannels.has('narramessenger') && narramessengerDraft.bindCommand.trim()) {
        try {
          const r = await api.bindNarramessenger(agentId, narramessengerDraft.bindCommand.trim());
          if (!r.success) failures.push('NarraMessenger');
        } catch {
          failures.push('NarraMessenger');
        }
      }

      // Skills — best-effort per pick.
      for (const skillId of selectedSkills.keys()) {
        try {
          await api.installMarketplaceSkill(skillId, agentId);
        } catch {
          failures.push(selectedSkills.get(skillId)?.name || skillId);
        }
      }

      if (failures.length) {
        await notifyError(
          t('pages.createAgent.partialFailureNotice', { detail: failures.join(', ') }),
        );
      }
      navigate('/app/chat');
    } catch (e) {
      setError(e instanceof Error ? e.message : t('pages.createAgent.createFailed'));
    } finally {
      setBusy(false);
    }
  }, [
    name, description, agentDraft, helperDraft, framework,
    agentInitial, helperInitial, frameworkInitial,
    discordDraft, telegramDraft, slackDraft, larkDraft, haDraft, narramessengerDraft, connectedChannels,
    selectedSkills, createAgent, notifyError, navigate, t,
  ]);

  const selectedSkillsList = useMemo(() => Array.from(selectedSkills.values()), [selectedSkills]);

  return (
    <Dialog
      isOpen
      onClose={() => navigate('/app/chat')}
      title={t('pages.createAgent.title')}
      size="3xl"
      bg="var(--nm-card)"
    >
      {dialog}
      <DialogContent className="flex flex-col gap-5">
        {step === 'provider' && (
          <div className="flex flex-col gap-4">
            <div className="flex flex-col gap-1">
              <span className={boxTitleCls}>{t('pages.createAgent.providerStepTitle')}</span>
              <span className="text-[13px] text-[var(--nm-ink50)]">
                {t('pages.createAgent.providerStepSubtitle')}
              </span>
            </div>

            <div className="flex flex-col gap-2">
              {bindableProviders.map((prov) => {
                const Icon = getProtocolBrandIcon(prov.protocol);
                const isCli = ['oauth', 'oauth_token'].includes(prov.auth_type);
                const selected = prov.provider_id === agentDraft.provider_id;
                return (
                  <button
                    key={prov.provider_id}
                    type="button"
                    onClick={() => selectProvider(prov.provider_id)}
                    className={cn(
                      'flex items-center justify-between gap-3 rounded-[var(--radius-md)] border p-3 text-left transition-colors',
                      selected
                        ? 'border-[var(--nm-ink)] bg-[var(--nm-card)]'
                        : 'border-[var(--border-subtle)] bg-[var(--nm-card)] hover:border-[var(--border-strong)]',
                    )}
                  >
                    <span className="flex items-center gap-3 min-w-0">
                      {Icon && <Icon className="h-5 w-5 shrink-0" />}
                      <span className="min-w-0">
                        <span className="block truncate text-[13px] font-medium text-[var(--nm-ink)]">
                          {prov.name}
                        </span>
                        <span className="block text-[11px] text-[var(--nm-ink30)]">
                          {isCli ? t('pages.createAgent.providerRowCli') : t('pages.createAgent.providerRowApiKey')}
                        </span>
                      </span>
                    </span>
                    <span
                      className={cn(
                        'h-2 w-2 shrink-0 rounded-full',
                        prov.is_active ? 'bg-[var(--color-success)]' : 'bg-[var(--nm-ink30)]',
                      )}
                    />
                  </button>
                );
              })}
            </div>

            <div className="flex flex-col gap-2">
              <span className={fieldLabel}>{t('pages.createAgent.addProviderTitle')}</span>
              <div className="flex gap-2">
                <button
                  type="button"
                  onClick={() => setAddMethod(addMethod === 'apikey' ? null : 'apikey')}
                  className={cn(
                    'flex flex-1 items-center justify-center gap-2 rounded-[var(--radius-sm)] border p-3 text-[13px] transition-colors',
                    addMethod === 'apikey'
                      ? 'border-[var(--nm-ink)] bg-[var(--nm-card)]'
                      : 'border-[var(--border-subtle)] bg-[var(--nm-card)] hover:border-[var(--border-strong)]',
                  )}
                >
                  <Key className="h-4 w-4" />
                  {t('pages.createAgent.addProviderApiKey')}
                </button>
                {!netmindOnly && (
                  <button
                    type="button"
                    onClick={() => setAddMethod(addMethod === 'cli' ? null : 'cli')}
                    className={cn(
                      'flex flex-1 items-center justify-center gap-2 rounded-[var(--radius-sm)] border p-3 text-[13px] transition-colors',
                      addMethod === 'cli'
                        ? 'border-[var(--nm-ink)] bg-[var(--nm-card)]'
                        : 'border-[var(--border-subtle)] bg-[var(--nm-card)] hover:border-[var(--border-strong)]',
                    )}
                  >
                    <Terminal className="h-4 w-4" />
                    {t('pages.createAgent.addProviderCli')}
                  </button>
                )}
              </div>

              {addMethod === 'apikey' && (
                <div className="flex flex-col gap-3 rounded-[var(--radius-sm)] border border-[var(--nm-hairline)] bg-[var(--nm-paper)] p-3.5">
                  <div className="flex gap-1">
                    <button
                      type="button"
                      onClick={() => setApiKeySubTab('quick')}
                      className={cn(
                        'flex-1 rounded-[var(--radius-sm)] border py-1.5 text-[12px]',
                        apiKeySubTab === 'quick'
                          ? 'border-[var(--nm-ink)] text-[var(--nm-ink)]'
                          : 'border-transparent text-[var(--nm-ink50)]',
                      )}
                    >
                      {t('settings.provider.tabApiKey')}
                    </button>
                    <button
                      type="button"
                      onClick={() => setApiKeySubTab('custom')}
                      className={cn(
                        'flex-1 rounded-[var(--radius-sm)] border py-1.5 text-[12px]',
                        apiKeySubTab === 'custom'
                          ? 'border-[var(--nm-ink)] text-[var(--nm-ink)]'
                          : 'border-transparent text-[var(--nm-ink50)]',
                      )}
                    >
                      {t('settings.provider.tabCustom')}
                    </button>
                  </div>
                  {apiKeySubTab === 'quick'
                    ? <OneKeyOnboard onComplete={handleProviderAdded} />
                    : <CustomEndpointForm onComplete={handleProviderAdded} />}
                </div>
              )}

              {addMethod === 'cli' && (
                <div className="flex flex-col gap-3 rounded-[var(--radius-sm)] border border-[var(--nm-hairline)] bg-[var(--nm-paper)] p-3.5">
                  <CliSignInPanel providers={bindableProviders} onComplete={handleProviderAdded} />
                </div>
              )}
            </div>
          </div>
        )}

        {step === 'details' && (
          <>
            <div className={boxCls}>
              <span className={boxTitleCls}>{t('pages.createAgent.awarenessSectionTitle')} *</span>
              <div className="flex flex-col gap-1.5">
                <span className={fieldLabel}>* {t('pages.createAgent.nameLabel')}</span>
                <input
                  value={name}
                  onChange={(e) => setName(e.target.value)}
                  placeholder={t('pages.createAgent.namePlaceholder')}
                  className={cn(inputCls, 'h-9')}
                />
              </div>
              <div className="flex flex-col gap-1.5">
                <span className={fieldLabel}>{t('pages.createAgent.descLabel')}</span>
                <textarea
                  value={description}
                  onChange={(e) => setDescription(e.target.value)}
                  placeholder={t('pages.createAgent.descPlaceholder')}
                  rows={6}
                  className={cn(
                    inputCls,
                    'min-h-[140px] resize-y py-2.5 font-mono leading-relaxed',
                  )}
                />
              </div>
            </div>

            <div className={boxCls}>
              <span className={boxTitleCls}>{t('pages.createAgent.engineSectionTitle')} *</span>

              {/* Agent slot */}
              <div className="flex flex-col gap-3">
                <div className="flex items-center justify-between gap-2 rounded-[var(--radius-sm)] border border-[var(--nm-hairline)] bg-[var(--nm-card)] px-3 py-2">
                  <span className="flex items-center gap-2 min-w-0 text-[13px] text-[var(--nm-ink)]">
                    {(() => {
                      const prov = providers[agentDraft.provider_id];
                      const Icon = prov ? getProtocolBrandIcon(prov.protocol) : null;
                      const fwLabel = AGENT_FRAMEWORKS.find((f) => f.id === framework)?.label || framework;
                      return (
                        <>
                          {Icon && <Icon className="h-4 w-4 shrink-0" />}
                          <span className="truncate">
                            {prov ? `${prov.name} · ${fwLabel}` : t('pages.settings.modelDefaults.selectProvider')}
                          </span>
                        </>
                      );
                    })()}
                  </span>
                  <button
                    type="button"
                    onClick={() => setStep('provider')}
                    className="shrink-0 font-mono text-[12px] text-[var(--nm-ink50)] underline underline-offset-2 hover:text-[var(--nm-ink)]"
                  >
                    {t('pages.createAgent.changeProvider')}
                  </button>
                </div>

                <div className="grid grid-cols-2 gap-3">
                  <div className="flex flex-col gap-1.5">
                    <span className={fieldLabel}>{t('pages.settings.modelDefaults.model')}</span>
                    <IconSelect
                      value={agentDraft.model}
                      disabled={!agentDraft.provider_id}
                      placeholder={t('pages.settings.modelDefaults.selectModel')}
                      options={(providers[agentDraft.provider_id]
                        ? getModelsForSlot(providers[agentDraft.provider_id], 'agent', framework, {})
                        : []
                      ).map((m) => ({ value: m.model_id, label: m.display_name, Icon: getModelBrandIcon(m.model_id) }))}
                      onChange={(model) => setAgentDraft((d) => ({ ...d, model }))}
                    />
                  </div>
                  <div className="flex flex-col gap-1.5">
                    <span className={fieldLabel}>{t('pages.settings.modelDefaults.thinking')}</span>
                    <select
                      className={selectCls}
                      value={agentDraft.thinking}
                      onChange={(e) => setAgentDraft((d) => ({ ...d, thinking: e.target.value }))}
                    >
                      <option value="">{t('pages.settings.modelDefaults.autoDefault')}</option>
                      <option value="on">{t('pages.settings.modelDefaults.on')}</option>
                      <option value="off">{t('pages.settings.modelDefaults.off')}</option>
                    </select>
                  </div>
                  <div className="flex flex-col gap-1.5">
                    <span className={fieldLabel}>{t('pages.settings.modelDefaults.reasoningEffort')}</span>
                    <select
                      className={selectCls}
                      value={agentDraft.reasoning_effort}
                      onChange={(e) => setAgentDraft((d) => ({ ...d, reasoning_effort: e.target.value }))}
                    >
                      <option value="">{t('pages.settings.modelDefaults.autoDefault')}</option>
                      <option value="low">{t('pages.settings.modelDefaults.low')}</option>
                      <option value="medium">{t('pages.settings.modelDefaults.medium')}</option>
                      <option value="high">{t('pages.settings.modelDefaults.high')}</option>
                      <option value="max">{t('pages.settings.modelDefaults.max')}</option>
                    </select>
                  </div>
                </div>
              </div>

              {/* Helper slot */}
              <div className="flex flex-col gap-1.5 pt-1 border-t border-[var(--nm-hairline)]">
                <span className={cn(fieldLabel, 'mt-3')}>{t('pages.settings.modelDefaults.helperTitle')}</span>
                <div className="grid grid-cols-2 gap-3">
                  <IconSelect
                    value={helperDraft.provider_id}
                    disabled={loadingEngine}
                    placeholder={t('pages.settings.modelDefaults.selectProvider')}
                    options={helperProviders.map((p) => ({
                      value: p.provider_id,
                      label: p.name,
                      Icon: getProtocolBrandIcon(p.protocol),
                    }))}
                    onChange={(pid) => {
                      const prov = providers[pid];
                      const models = prov ? getModelsForSlot(prov, 'helper_llm', null, {}) : [];
                      const model = defaultHelperModel(prov?.source, prov?.protocol, models.map((m) => m.model_id));
                      setHelperDraft({ provider_id: pid, model });
                    }}
                  />
                  <IconSelect
                    value={helperDraft.model}
                    disabled={!helperDraft.provider_id}
                    placeholder={t('pages.settings.modelDefaults.selectModel')}
                    options={(providers[helperDraft.provider_id]
                      ? getModelsForSlot(providers[helperDraft.provider_id], 'helper_llm', null, {})
                      : []
                    ).map((m) => ({ value: m.model_id, label: m.display_name, Icon: getModelBrandIcon(m.model_id) }))}
                    onChange={(model) => setHelperDraft((d) => ({ ...d, model }))}
                  />
                </div>
              </div>
            </div>

            <div className={boxCls}>
              <span className={boxTitleCls}>
                {t('pages.createAgent.channelSectionTitle')}{' '}
                <span className="font-normal text-[var(--nm-ink30)]">
                  {t('pages.createAgent.optionalSuffix')}
                </span>
              </span>
              <div className="flex flex-wrap items-start gap-3">
                {CHANNEL_ICONS.map(({ key, label, Icon }) => {
                  const expanded = expandedChannel === key;
                  const connected = connectedChannels.has(key);
                  return (
                    <button
                      key={key}
                      type="button"
                      title={label}
                      onClick={() => toggleChannel(key)}
                      className={cn(
                        'relative flex w-[76px] flex-col items-center justify-center gap-1.5 rounded-[var(--radius-md)] border p-3 transition-colors',
                        expanded
                          ? 'border-[var(--nm-ink)] bg-[var(--nm-card)]'
                          : 'border-[var(--border-subtle)] bg-[var(--nm-card)] hover:border-[var(--border-strong)]',
                      )}
                    >
                      {connected && (
                        <span
                          title={t('pages.createAgent.channelConfigured')}
                          className="absolute right-1.5 top-1.5 h-2 w-2 rounded-full bg-[var(--color-success)]"
                        />
                      )}
                      <Icon
                        className={cn(
                          // Every icon now carries its own hardcoded brand color
                          // (not currentColor — Owner wants them recognizable as
                          // real colored logos), so dimming has to be opacity /
                          // grayscale for all of them uniformly, not a text-color
                          // swap that only ever affected the vector ones.
                          'h-6 w-6 transition-[filter,opacity]',
                          expanded ? 'opacity-100 grayscale-0' : 'opacity-40 grayscale',
                        )}
                      />
                      <span className="max-w-full truncate text-[10px] text-[var(--nm-ink50)]">
                        {label}
                      </span>
                    </button>
                  );
                })}
              </div>

              {expandedChannel === 'discord' && (
                <div className="flex flex-col gap-3 rounded-[var(--radius-sm)] border border-[var(--nm-hairline)] bg-[var(--nm-paper)] p-3.5">
                  <span className={fieldLabel}>Discord</span>
                  <div className="flex flex-col gap-1.5">
                    <span className={fieldLabel}>{t('awareness.discord.botToken')}</span>
                    <input
                      value={discordDraft.botToken}
                      onChange={(e) => setDiscordDraft((d) => ({ ...d, botToken: e.target.value }))}
                      className={cn(inputCls, 'h-9')}
                    />
                  </div>
                  <div className="flex flex-col gap-1.5">
                    <span className={fieldLabel}>{t('awareness.discord.ownerIdLabel')}</span>
                    <input
                      value={discordDraft.ownerUserId}
                      onChange={(e) => setDiscordDraft((d) => ({ ...d, ownerUserId: e.target.value }))}
                      placeholder={t('awareness.discord.ownerIdPlaceholder')}
                      className={cn(inputCls, 'h-9')}
                    />
                  </div>
                  {channelError && <p className="text-[12px] text-[var(--color-error)]">{channelError}</p>}
                  <Button size="sm" onClick={() => connectChannel('discord')} className="self-start">
                    {t('awareness.common.bindBot')}
                  </Button>
                </div>
              )}

              {expandedChannel === 'telegram' && (
                <div className="flex flex-col gap-3 rounded-[var(--radius-sm)] border border-[var(--nm-hairline)] bg-[var(--nm-paper)] p-3.5">
                  <span className={fieldLabel}>Telegram</span>
                  <div className="flex flex-col gap-1.5">
                    <span className={fieldLabel}>{t('awareness.discord.botToken')}</span>
                    <input
                      value={telegramDraft.botToken}
                      onChange={(e) => setTelegramDraft((d) => ({ ...d, botToken: e.target.value }))}
                      placeholder={t('awareness.telegram.tokenPlaceholder')}
                      className={cn(inputCls, 'h-9')}
                    />
                  </div>
                  <div className="flex flex-col gap-1.5">
                    <span className={fieldLabel}>{t('awareness.telegram.usernameLabel')}</span>
                    <input
                      value={telegramDraft.ownerUsername}
                      onChange={(e) => setTelegramDraft((d) => ({ ...d, ownerUsername: e.target.value }))}
                      placeholder={t('awareness.telegram.usernamePlaceholder')}
                      className={cn(inputCls, 'h-9')}
                    />
                  </div>
                  {channelError && <p className="text-[12px] text-[var(--color-error)]">{channelError}</p>}
                  <Button size="sm" onClick={() => connectChannel('telegram')} className="self-start">
                    {t('awareness.common.bindBot')}
                  </Button>
                </div>
              )}

              {expandedChannel === 'slack' && (
                <div className="flex flex-col gap-3 rounded-[var(--radius-sm)] border border-[var(--nm-hairline)] bg-[var(--nm-paper)] p-3.5">
                  <span className={fieldLabel}>Slack</span>
                  <div className="flex flex-col gap-1.5">
                    <span className={fieldLabel}>{t('awareness.discord.botToken')}</span>
                    <input
                      value={slackDraft.botToken}
                      onChange={(e) => setSlackDraft((d) => ({ ...d, botToken: e.target.value }))}
                      placeholder={t('awareness.slack.botTokenPlaceholder')}
                      className={cn(inputCls, 'h-9')}
                    />
                  </div>
                  <div className="flex flex-col gap-1.5">
                    <span className={fieldLabel}>{t('awareness.slack.appToken')}</span>
                    <input
                      value={slackDraft.appToken}
                      onChange={(e) => setSlackDraft((d) => ({ ...d, appToken: e.target.value }))}
                      placeholder={t('awareness.slack.appTokenPlaceholder')}
                      className={cn(inputCls, 'h-9')}
                    />
                  </div>
                  <div className="flex flex-col gap-1.5">
                    <span className={fieldLabel}>{t('awareness.slack.emailLabel')}</span>
                    <input
                      value={slackDraft.ownerEmail}
                      onChange={(e) => setSlackDraft((d) => ({ ...d, ownerEmail: e.target.value }))}
                      placeholder={t('awareness.slack.emailPlaceholder')}
                      className={cn(inputCls, 'h-9')}
                    />
                  </div>
                  {channelError && <p className="text-[12px] text-[var(--color-error)]">{channelError}</p>}
                  <Button size="sm" onClick={() => connectChannel('slack')} className="self-start">
                    {t('awareness.common.bindBot')}
                  </Button>
                </div>
              )}

              {expandedChannel === 'lark' && (
                <div className="flex flex-col gap-3 rounded-[var(--radius-sm)] border border-[var(--nm-hairline)] bg-[var(--nm-paper)] p-3.5">
                  <span className={fieldLabel}>Lark / Feishu</span>
                  <div className="flex items-center gap-1.5">
                    <button
                      type="button"
                      onClick={() => setLarkDraft((d) => ({ ...d, brand: 'feishu' }))}
                      className={cn(
                        'h-8 flex-1 rounded-[var(--radius-sm)] border text-[12.5px]',
                        larkDraft.brand === 'feishu'
                          ? 'border-[var(--nm-ink)] bg-[var(--nm-card)] text-[var(--nm-ink)]'
                          : 'border-[var(--border-subtle)] text-[var(--nm-ink50)]',
                      )}
                    >
                      {t('awareness.lark.feishu')}
                    </button>
                    <button
                      type="button"
                      onClick={() => setLarkDraft((d) => ({ ...d, brand: 'lark' }))}
                      className={cn(
                        'h-8 flex-1 rounded-[var(--radius-sm)] border text-[12.5px]',
                        larkDraft.brand === 'lark'
                          ? 'border-[var(--nm-ink)] bg-[var(--nm-card)] text-[var(--nm-ink)]'
                          : 'border-[var(--border-subtle)] text-[var(--nm-ink50)]',
                      )}
                    >
                      {t('awareness.lark.larkInternational')}
                    </button>
                  </div>
                  <div className="flex flex-col gap-1.5">
                    <span className={fieldLabel}>{t('awareness.lark.appId')}</span>
                    <input
                      value={larkDraft.appId}
                      onChange={(e) => setLarkDraft((d) => ({ ...d, appId: e.target.value }))}
                      placeholder={t('awareness.lark.appIdPlaceholder')}
                      className={cn(inputCls, 'h-9')}
                    />
                  </div>
                  <div className="flex flex-col gap-1.5">
                    <span className={fieldLabel}>{t('awareness.lark.appSecret')}</span>
                    <input
                      value={larkDraft.appSecret}
                      onChange={(e) => setLarkDraft((d) => ({ ...d, appSecret: e.target.value }))}
                      className={cn(inputCls, 'h-9')}
                    />
                  </div>
                  <div className="flex flex-col gap-1.5">
                    <span className={fieldLabel}>{t('awareness.lark.ownerEmail')}</span>
                    <input
                      value={larkDraft.ownerEmail}
                      onChange={(e) => setLarkDraft((d) => ({ ...d, ownerEmail: e.target.value }))}
                      placeholder={t('awareness.lark.ownerEmailPlaceholder')}
                      className={cn(inputCls, 'h-9')}
                    />
                  </div>
                  {channelError && <p className="text-[12px] text-[var(--color-error)]">{channelError}</p>}
                  <Button size="sm" onClick={() => connectChannel('lark')} className="self-start">
                    {t('awareness.common.bindBot')}
                  </Button>
                </div>
              )}

              {expandedChannel === 'home_assistant' && (
                <div className="flex flex-col gap-3 rounded-[var(--radius-sm)] border border-[var(--nm-hairline)] bg-[var(--nm-paper)] p-3.5">
                  <span className={fieldLabel}>Home Assistant</span>
                  <div className="flex flex-col gap-1.5">
                    <span className={fieldLabel}>{t('awareness.homeAssistant.baseUrl')}</span>
                    <input
                      value={haDraft.baseUrl}
                      onChange={(e) => setHaDraft((d) => ({ ...d, baseUrl: e.target.value }))}
                      placeholder={t('awareness.homeAssistant.baseUrlPlaceholder')}
                      className={cn(inputCls, 'h-9')}
                    />
                  </div>
                  <div className="flex flex-col gap-1.5">
                    <span className={fieldLabel}>{t('awareness.homeAssistant.token')}</span>
                    <input
                      value={haDraft.token}
                      onChange={(e) => setHaDraft((d) => ({ ...d, token: e.target.value }))}
                      placeholder={t('awareness.homeAssistant.tokenPlaceholder')}
                      className={cn(inputCls, 'h-9')}
                    />
                  </div>
                  <label className="flex items-center gap-2 text-[12.5px] text-[var(--nm-ink)]">
                    <input
                      type="checkbox"
                      checked={haDraft.verifyTls}
                      onChange={(e) => setHaDraft((d) => ({ ...d, verifyTls: e.target.checked }))}
                    />
                    {t('awareness.homeAssistant.verifyTls')}
                  </label>
                  {channelError && <p className="text-[12px] text-[var(--color-error)]">{channelError}</p>}
                  <Button size="sm" onClick={() => connectChannel('home_assistant')} className="self-start">
                    {t('awareness.common.save')}
                  </Button>
                </div>
              )}

              {expandedChannel === 'wechat' && (
                <div className="flex flex-col gap-2 rounded-[var(--radius-sm)] border border-[var(--nm-hairline)] bg-[var(--nm-paper)] p-3.5">
                  <span className={fieldLabel}>WeChat</span>
                  <span className="text-[12px] text-[var(--nm-ink30)]">
                    {t('pages.createAgent.channelPlaceholderHint')}
                  </span>
                </div>
              )}

              {expandedChannel === 'narramessenger' && (
                <div className="flex flex-col gap-3 rounded-[var(--radius-sm)] border border-[var(--nm-hairline)] bg-[var(--nm-paper)] p-3.5">
                  <span className={fieldLabel}>NarraMessenger</span>
                  <div className="flex flex-col gap-1.5">
                    <span className={fieldLabel}>{t('pages.createAgent.narramessengerBindLabel')}</span>
                    <input
                      value={narramessengerDraft.bindCommand}
                      onChange={(e) => setNarramessengerDraft({ bindCommand: e.target.value })}
                      placeholder={t('pages.createAgent.narramessengerBindPlaceholder')}
                      className={cn(inputCls, 'h-9')}
                    />
                  </div>
                  {channelError && <p className="text-[12px] text-[var(--color-error)]">{channelError}</p>}
                  <Button size="sm" onClick={() => connectChannel('narramessenger')} className="self-start">
                    {t('awareness.common.bindBot')}
                  </Button>
                </div>
              )}
            </div>

            <div className={boxCls}>
              <span className={boxTitleCls}>
                {t('pages.createAgent.configSectionTitle')}{' '}
                <span className="font-normal text-[var(--nm-ink30)]">
                  {t('pages.createAgent.optionalSuffix')}
                </span>
              </span>

              {/* Skills */}
              <div className="flex flex-col gap-1.5">
                <span className={fieldLabel}>{t('pages.createAgent.skillsLabel')}</span>
                <Popover open={skillsOpen} onOpenChange={setSkillsOpen}>
                  <PopoverTrigger asChild>
                    <button
                      type="button"
                      className={cn(selectCls, 'flex items-center justify-between text-left')}
                    >
                      <span>
                        {selectedSkills.size > 0
                          ? t('pages.createAgent.skillsSelectedCount', { count: selectedSkills.size })
                          : t('pages.createAgent.skillsPlaceholder')}
                      </span>
                      <ChevronDown className="h-3.5 w-3.5 shrink-0 text-[var(--nm-ink30)]" />
                    </button>
                  </PopoverTrigger>
                  <PopoverContent align="start" className="z-[1100] w-[360px] p-2">
                    <div className="flex items-center gap-2 h-8 px-2.5 mb-2 rounded-[var(--radius-sm)] border border-[var(--border-subtle)] bg-[var(--nm-card)]">
                      <Search className="w-3 h-3 shrink-0 text-[var(--nm-ink30)]" />
                      <input
                        autoFocus
                        value={skillInput}
                        onChange={(e) => setSkillInput(e.target.value)}
                        placeholder={t('pages.createAgent.skillsSearchPlaceholder')}
                        className="flex-1 bg-transparent text-[12px] text-[var(--nm-ink)] placeholder:text-[var(--nm-ink30)] focus:outline-none"
                      />
                    </div>
                    <div className="max-h-[280px] overflow-y-auto flex flex-col gap-1">
                      {skillsLoading ? (
                        <div className="flex items-center justify-center py-4">
                          <Loader2 className="h-4 w-4 animate-spin text-[var(--nm-ink30)]" />
                        </div>
                      ) : (skillResults?.items?.length ?? 0) === 0 ? (
                        <div className="text-[12px] text-[var(--nm-ink50)] px-2 py-3 text-center">
                          {t('pages.createAgent.skillsNoResults')}
                        </div>
                      ) : (
                        skillResults!.items.map((item) => {
                          const checked = selectedSkills.has(item.skill_id);
                          return (
                            <label
                              key={item.skill_id}
                              className={cn(
                                'flex cursor-pointer items-start gap-2.5 rounded-[var(--radius-sm)] px-2.5 py-2 transition-colors',
                                checked ? 'bg-[var(--nm-card)]' : 'hover:bg-[var(--nm-card)]',
                              )}
                              onClick={() => toggleSkill(item)}
                            >
                              <span
                                className={cn(
                                  'mt-0.5 inline-flex h-3.5 w-3.5 shrink-0 items-center justify-center rounded-[2px] border text-[10px] leading-none',
                                  checked
                                    ? 'border-[var(--nm-ink)] bg-[var(--nm-ink)] text-[var(--nm-paper)]'
                                    : 'border-[var(--border-default)] bg-[var(--nm-card)] text-transparent',
                                )}
                              >
                                ✓
                              </span>
                              <span className="min-w-0 flex-1">
                                <span className="block truncate text-[12.5px] font-medium text-[var(--nm-ink)]">
                                  {item.name}
                                </span>
                                {item.description && (
                                  <span className="block truncate text-[11px] text-[var(--nm-ink30)]">
                                    {item.description}
                                  </span>
                                )}
                              </span>
                            </label>
                          );
                        })
                      )}
                    </div>
                  </PopoverContent>
                </Popover>
                {selectedSkillsList.length > 0 && (
                  <div className="flex flex-wrap gap-1.5">
                    {selectedSkillsList.map((s) => (
                      <span
                        key={s.skill_id}
                        className="inline-flex items-center gap-1 rounded-[var(--radius-sm)] bg-[var(--nm-card)] px-2 py-0.5 text-[11px] text-[var(--nm-ink)] border border-[var(--nm-hairline)]"
                      >
                        {s.name}
                      </span>
                    ))}
                  </div>
                )}
              </div>

              {/* MCP — disabled placeholder, no agent-independent catalog exists */}
              <div className="flex flex-col gap-1.5">
                <span className={fieldLabel}>{t('pages.createAgent.mcpLabel')}</span>
                <div className={cn(selectCls, 'flex items-center opacity-50 cursor-not-allowed select-none')}>
                  {t('pages.createAgent.mcpPlaceholder')}
                </div>
              </div>
            </div>
          </>
        )}
        {error && <p className="text-sm text-[var(--color-error)]">{error}</p>}
      </DialogContent>
      <DialogFooter>
        {step === 'provider' && (
          <>
            <Button variant="ghost" onClick={() => navigate('/app/chat')} disabled={busy}>
              {t('pages.createAgent.cancel')}
            </Button>
            <Button onClick={() => setStep('details')}>
              {t('pages.createAgent.next')}
            </Button>
          </>
        )}
        {step === 'details' && (
          <>
            <Button variant="ghost" onClick={() => setStep('provider')} disabled={busy}>
              {t('pages.createAgent.back')}
            </Button>
            <Button variant="ghost" onClick={() => navigate('/app/chat')} disabled={busy}>
              {t('pages.createAgent.cancel')}
            </Button>
            <Button onClick={handleCreate} disabled={!canCreate} className="gap-1.5">
              {busy && <Loader2 className="h-3.5 w-3.5 animate-spin" />}
              {busy ? t('pages.createAgent.creating') : t('pages.createAgent.createButton')}
            </Button>
          </>
        )}
      </DialogFooter>
    </Dialog>
  );
}
