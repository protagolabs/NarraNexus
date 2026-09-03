/**
 * @file_name: BuilderConfigPanel.tsx
 * @author: NetMind.AI
 * @date: 2026-09-03
 * @description: The creation studio's configuration panel — the right-hand
 * half of "describe what you want, watch the panel fill in".
 *
 * Everything here reflects the REAL agent, not a draft. The studio runs on the
 * agent the user just created, so name and awareness are read from the agent
 * and written straight back; there is no staging area to reconcile and no
 * "apply" step. That is the whole point of the path.
 *
 * Layout follows the Owner's 2026-09-03 reference: identity is NAME ONLY (no
 * avatar — the project has no agent-avatar capability, sidebar avatars are
 * generated from identity; no description field — it is machine-facing copy
 * that other agents read for routing, so the conversation writes it and the
 * Agent Profile page displays it). The instruction box is labelled with the
 * field it actually writes, `awareness`, rather than a synonym.
 *
 * Skills and Channels embed the SAME sections the drawer's own Skills and
 * Channels tabs render. Reuse over re-derivation: a second channel-status
 * reader or skill list would drift from those tabs the first time either
 * changes. The cost is that this tab pulls their lazy chunks too — accepted,
 * because the Owner wants both configurable without leaving the studio.
 *
 * The conversation's SUGGESTIONS sit above each real section. Suggesting and
 * installing stay separate on purpose: installing a skill copies files into
 * the agent's workspace (a model that changes its mind would install then
 * uninstall in front of the user), and binding a channel needs a credential,
 * which is the user's to paste and must never reach the model.
 *
 * Text fields save on BLUR, not per keystroke: a PUT per character would race
 * the model's own writes on the same fields.
 */
import { useCallback, useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { useQueryClient } from '@tanstack/react-query';
import { Check, Loader2, Plus } from 'lucide-react';
import { api } from '@/lib/api';
import { Button, Input, Textarea, ScrollArea } from '@/components/ui';
import { AwarenessPanel } from '@/components/awareness';
import { SkillsPanel } from '@/components/skills';
import { useConfigStore, usePreloadStore } from '@/stores';
import { closeStudio, readRecommendations } from '@/lib/builderSession';
import { AGENT_TEXT_MAX_LENGTH } from '@/lib/agentLimits';

interface BuilderConfigPanelProps {
  agentId: string;
}

export function BuilderConfigPanel({ agentId }: BuilderConfigPanelProps) {
  const { t } = useTranslation();
  const qc = useQueryClient();
  const agents = useConfigStore((s) => s.agents);
  const refreshAgents = useConfigStore((s) => s.refreshAgents);
  const awareness = usePreloadStore((s) => s.awareness);
  const refreshAwareness = usePreloadStore((s) => s.refreshAwareness);

  const agent = agents.find((a) => a.agent_id === agentId);
  const recommendations = readRecommendations(agentId);

  // Local mirrors of the two live fields. The server value wins whenever it
  // changes underneath — that is how the model's writes appear here; typing
  // overrides locally until blur commits or the next server value lands.
  const [name, setName] = useState(agent?.name ?? '');
  const [instructions, setInstructions] = useState(awareness ?? '');
  const [saving, setSaving] = useState(false);
  const [installing, setInstalling] = useState<string | null>(null);
  const [installed, setInstalled] = useState<string[]>([]);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => setName(agent?.name ?? ''), [agent?.name]);
  useEffect(() => setInstructions(awareness ?? ''), [awareness]);

  const commitName = useCallback(async () => {
    const next = name.trim();
    if (next === (agent?.name ?? '')) return;
    setSaving(true);
    setError(null);
    try {
      const res = await api.updateAgent(agentId, next, agent?.description ?? '');
      if (!res.success) throw new Error(res.message ?? res.error ?? 'update failed');
      await refreshAgents();
    } catch (e) {
      setError(String(e));
    } finally {
      setSaving(false);
    }
  }, [name, agent?.name, agent?.description, agentId, refreshAgents]);

  const commitAwareness = useCallback(async () => {
    if (instructions === (awareness ?? '')) return;
    setSaving(true);
    setError(null);
    try {
      const res = await api.updateAwareness(agentId, instructions);
      if (!res.success) throw new Error(res.message ?? res.error ?? 'update failed');
      await refreshAwareness(agentId, true);
    } catch (e) {
      setError(String(e));
    } finally {
      setSaving(false);
    }
  }, [instructions, awareness, agentId, refreshAwareness]);

  const install = useCallback(
    async (skillId: string) => {
      setInstalling(skillId);
      setError(null);
      try {
        await api.installMarketplaceSkill(skillId, agentId);
        setInstalled((prev) => [...prev, skillId]);
        // The embedded Skills section reads the same list through react-query.
        await qc.invalidateQueries({ queryKey: ['skills'] });
      } catch (e) {
        setError(String(e));
      } finally {
        setInstalling(null);
      }
    },
    [agentId, qc],
  );

  return (
    <div className="flex h-full min-h-0 flex-col">
      <ScrollArea className="flex-1 min-h-0">
        <div className="space-y-8 px-4 py-4">
          <p className="text-xs leading-relaxed" style={{ color: 'var(--text-tertiary)' }}>
            {t('builder.panel.intro')}
          </p>

          {/* ── Identity: name only ── */}
          <Section title={t('builder.panel.identity')} hint={t('builder.panel.identityHint')}>
            <FieldCard>
              <label className="block space-y-1.5 p-3">
                <span className="text-xs" style={{ color: 'var(--text-secondary)' }}>
                  {t('builder.panel.name')}
                </span>
                <Input
                  value={name}
                  maxLength={AGENT_TEXT_MAX_LENGTH}
                  placeholder={t('builder.panel.namePlaceholder')}
                  onChange={(e) => setName(e.target.value)}
                  onBlur={() => void commitName()}
                />
              </label>
            </FieldCard>
          </Section>

          {/* ── Behaviour: the awareness field, named after what it writes ── */}
          <Section title={t('builder.panel.behaviour')} hint={t('builder.panel.behaviourHint')}>
            <FieldCard>
              <label className="block space-y-1.5 p-3">
                <span className="text-xs" style={{ color: 'var(--text-secondary)' }}>
                  {t('builder.panel.awareness')}
                </span>
                <Textarea
                  rows={12}
                  value={instructions}
                  placeholder={t('builder.panel.awarenessPlaceholder')}
                  onChange={(e) => setInstructions(e.target.value)}
                  onBlur={() => void commitAwareness()}
                  className="font-mono text-[12px] leading-relaxed"
                />
              </label>
            </FieldCard>
          </Section>

          {/* ── Skills: suggestions, then the real section ── */}
          <Section title={t('builder.panel.skills')} hint={t('builder.panel.skillsHint')}>
            {recommendations.skill_ids.length > 0 && (
              <div className="space-y-2">
                <SuggestionLabel>{t('builder.panel.suggested')}</SuggestionLabel>
                {recommendations.skill_ids.map((skillId) => {
                  const done = installed.includes(skillId);
                  return (
                    <div
                      key={skillId}
                      className="flex items-center gap-2 rounded-[var(--radius-lg)] px-3 py-2"
                      style={{ border: '1px solid var(--nm-hairline)', background: 'var(--bg-primary)' }}
                    >
                      <span
                        className="min-w-0 flex-1 truncate text-[12px]"
                        style={{ fontFamily: 'var(--font-mono)', color: 'var(--text-secondary)' }}
                      >
                        {skillId}
                      </span>
                      <Button
                        variant="outline"
                        size="sm"
                        disabled={done || installing === skillId}
                        onClick={() => void install(skillId)}
                        className="gap-1.5 shrink-0"
                      >
                        {installing === skillId ? (
                          <Loader2 className="h-3 w-3 animate-spin" />
                        ) : done ? (
                          <Check className="h-3 w-3" />
                        ) : (
                          <Plus className="h-3 w-3" />
                        )}
                        {done ? t('builder.panel.installed') : t('builder.panel.install')}
                      </Button>
                    </div>
                  );
                })}
              </div>
            )}
            <EmbeddedSection>
              <SkillsPanel embedded section="skills" />
            </EmbeddedSection>
          </Section>

          {/* ── Channels: suggestions, then the real section ── */}
          <Section title={t('builder.panel.channels')} hint={t('builder.panel.channelsHint')}>
            {recommendations.channels.length > 0 && (
              <div className="space-y-2">
                <SuggestionLabel>{t('builder.panel.suggested')}</SuggestionLabel>
                <div className="flex flex-wrap gap-1.5">
                  {recommendations.channels.map((channel) => (
                    <span
                      key={channel}
                      className="rounded-full px-2.5 py-1 text-[11px]"
                      style={{
                        fontFamily: 'var(--font-mono)',
                        border: '1px solid var(--nm-hairline)',
                        background: 'var(--nm-paper-warm)',
                        color: 'var(--text-secondary)',
                      }}
                    >
                      {channel}
                    </span>
                  ))}
                </div>
              </div>
            )}
            {/* The credential is pasted here, user → backend. It never enters
                the conversation envelope. */}
            <EmbeddedSection>
              <AwarenessPanel embedded section="channels" />
            </EmbeddedSection>
          </Section>

          {error && (
            <p className="text-[11px] leading-relaxed" style={{ color: 'var(--color-error)' }}>
              {error}
            </p>
          )}
        </div>
      </ScrollArea>

      <div
        className="flex items-center justify-between gap-3 border-t px-4 py-3"
        style={{ borderColor: 'var(--nm-hairline)' }}
      >
        <span className="text-[11px]" style={{ color: 'var(--text-tertiary)' }}>
          {saving ? t('builder.panel.saving') : t('builder.panel.savedHint')}
        </span>
        {/* No "discard": every field here is already written to the agent, so
            there is nothing to roll back. Finishing only leaves the studio —
            the drawer close is owned by the caller that opened it. */}
        <Button size="sm" onClick={() => closeStudio(agentId)}>
          {t('builder.panel.done')}
        </Button>
      </div>
    </div>
  );
}

function Section({
  title,
  hint,
  children,
}: {
  title: string;
  hint?: string;
  children: React.ReactNode;
}) {
  return (
    <section className="space-y-2.5">
      <div>
        <h3 className="text-[13px] font-semibold" style={{ color: 'var(--text-primary)' }}>
          {title}
        </h3>
        {hint && (
          <p className="mt-0.5 text-[11px] leading-relaxed" style={{ color: 'var(--text-tertiary)' }}>
            {hint}
          </p>
        )}
      </div>
      {children}
    </section>
  );
}

function FieldCard({ children }: { children: React.ReactNode }) {
  return (
    <div
      className="rounded-[var(--radius-xl)] overflow-hidden"
      style={{ border: '1px solid var(--nm-hairline)', background: 'var(--bg-primary)' }}
    >
      {children}
    </div>
  );
}

/** Hosts a reused drawer section without its own outer card chrome. */
function EmbeddedSection({ children }: { children: React.ReactNode }) {
  return (
    <div
      className="rounded-[var(--radius-xl)] overflow-hidden"
      style={{ border: '1px solid var(--nm-hairline)', background: 'var(--bg-primary)' }}
    >
      {children}
    </div>
  );
}

function SuggestionLabel({ children }: { children: React.ReactNode }) {
  return (
    <span
      className="block text-[10px] font-medium uppercase tracking-[0.13em]"
      style={{ fontFamily: 'var(--font-mono)', color: 'var(--text-tertiary)' }}
    >
      {children}
    </span>
  );
}
