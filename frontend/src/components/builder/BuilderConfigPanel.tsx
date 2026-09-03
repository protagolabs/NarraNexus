/**
 * @file_name: BuilderConfigPanel.tsx
 * @author: NetMind.AI
 * @date: 2026-09-03
 * @description: The creation studio's configuration panel — the right-hand
 * half of "describe what you want, watch the panel fill in".
 *
 * Everything here reflects the REAL agent, not a draft. The studio runs on the
 * agent the user just created, so identity and instructions are read from the
 * agent and written straight back; there is no staging area to reconcile and
 * no "apply" step for those fields. That is the whole point of the path.
 *
 * The panel deliberately does NOT re-mount AwarenessPanel / SkillsPanel /
 * IMChannelsSection. Those are the drawer's own atomic tabs, and stacking
 * three heavy panels inside a fourth would fight the one-tab-one-panel IA and
 * triple the lazy chunks this tab pulls. Instead the studio shows the fields
 * the conversation actually drives, and hands off to those tabs for the deep
 * work (installing a skill's config, pasting a bot token).
 *
 * Skills and channels appear as RECOMMENDATIONS with a human click, never as
 * automatic writes:
 *   - installing a skill copies files into the agent's workspace, and a model
 *     that changes its mind would install-then-uninstall in front of the user;
 *   - binding a channel needs a credential, which is the user's to supply and
 *     must never reach the model.
 *
 * Text fields save on BLUR, not per keystroke: a PUT per character would race
 * the model's own writes on the same fields.
 */
import { useCallback, useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { useQueryClient } from '@tanstack/react-query';
import { Check, Loader2, Plus, Radio } from 'lucide-react';
import { api } from '@/lib/api';
import { Button, Input, Textarea, ScrollArea } from '@/components/ui';
import { useConfigStore, usePreloadStore, useUIStore } from '@/stores';
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
  const requestPanel = useUIStore((s) => s.requestPanel);

  const agent = agents.find((a) => a.agent_id === agentId);
  const recommendations = readRecommendations(agentId);

  // Local mirrors of the three live fields. Server value wins whenever it
  // changes underneath (that is how the model's writes show up); typing
  // overrides locally until blur commits or the next server value lands.
  const [name, setName] = useState(agent?.name ?? '');
  const [description, setDescription] = useState(agent?.description ?? '');
  const [instructions, setInstructions] = useState(awareness ?? '');
  const [saving, setSaving] = useState<null | 'identity' | 'awareness'>(null);
  const [installing, setInstalling] = useState<string | null>(null);
  const [installed, setInstalled] = useState<string[]>([]);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => setName(agent?.name ?? ''), [agent?.name]);
  useEffect(() => setDescription(agent?.description ?? ''), [agent?.description]);
  useEffect(() => setInstructions(awareness ?? ''), [awareness]);

  const commitIdentity = useCallback(async () => {
    const nextName = name.trim();
    const nextDesc = description.trim();
    if (nextName === (agent?.name ?? '') && nextDesc === (agent?.description ?? '')) return;
    setSaving('identity');
    setError(null);
    try {
      const res = await api.updateAgent(agentId, nextName, nextDesc);
      if (!res.success) throw new Error(res.message ?? res.error ?? 'update failed');
      await refreshAgents();
    } catch (e) {
      setError(String(e));
    } finally {
      setSaving(null);
    }
  }, [name, description, agent?.name, agent?.description, agentId, refreshAgents]);

  const commitInstructions = useCallback(async () => {
    if (instructions === (awareness ?? '')) return;
    setSaving('awareness');
    setError(null);
    try {
      const res = await api.updateAwareness(agentId, instructions);
      if (!res.success) throw new Error(res.message ?? res.error ?? 'update failed');
      await refreshAwareness(agentId, true);
    } catch (e) {
      setError(String(e));
    } finally {
      setSaving(null);
    }
  }, [instructions, awareness, agentId, refreshAwareness]);

  const install = useCallback(
    async (skillId: string) => {
      setInstalling(skillId);
      setError(null);
      try {
        await api.installMarketplaceSkill(skillId, agentId);
        setInstalled((prev) => [...prev, skillId]);
        // The Skills tab reads the same list through react-query.
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
        <div className="space-y-7 px-4 py-4">
          <p className="text-xs leading-relaxed" style={{ color: 'var(--text-tertiary)' }}>
            {t('builder.panel.intro')}
          </p>

          {/* ── Identity ── */}
          <section className="space-y-3">
            <SectionLabel>{t('builder.panel.identity')}</SectionLabel>
            <label className="block space-y-1.5">
              <span className="text-xs" style={{ color: 'var(--text-secondary)' }}>
                {t('builder.panel.name')}
              </span>
              <Input
                value={name}
                maxLength={AGENT_TEXT_MAX_LENGTH}
                placeholder={t('builder.panel.namePlaceholder')}
                onChange={(e) => setName(e.target.value)}
                onBlur={() => void commitIdentity()}
              />
            </label>
            <label className="block space-y-1.5">
              <span className="text-xs" style={{ color: 'var(--text-secondary)' }}>
                {t('builder.panel.description')}
              </span>
              <Textarea
                rows={3}
                value={description}
                maxLength={AGENT_TEXT_MAX_LENGTH}
                placeholder={t('builder.panel.descriptionPlaceholder')}
                onChange={(e) => setDescription(e.target.value)}
                onBlur={() => void commitIdentity()}
              />
              <span
                className="block text-right text-[10px]"
                style={{ fontFamily: 'var(--font-mono)', color: 'var(--text-tertiary)' }}
              >
                {description.length} / {AGENT_TEXT_MAX_LENGTH}
              </span>
            </label>
          </section>

          {/* ── Instructions ── */}
          <section className="space-y-3">
            <SectionLabel>{t('builder.panel.behaviour')}</SectionLabel>
            <label className="block space-y-1.5">
              <span className="text-xs" style={{ color: 'var(--text-secondary)' }}>
                {t('builder.panel.instructions')}
              </span>
              <Textarea
                rows={12}
                value={instructions}
                placeholder={t('builder.panel.instructionsPlaceholder')}
                onChange={(e) => setInstructions(e.target.value)}
                onBlur={() => void commitInstructions()}
                className="font-mono text-[12px] leading-relaxed"
              />
            </label>
          </section>

          {/* ── Recommendations ── */}
          <section className="space-y-3">
            <SectionLabel>{t('builder.panel.recommended')}</SectionLabel>
            <p className="text-[11px] leading-relaxed" style={{ color: 'var(--text-tertiary)' }}>
              {t('builder.panel.recommendedHint')}
            </p>

            {recommendations.skill_ids.length === 0 && recommendations.channels.length === 0 && (
              <p className="text-xs" style={{ color: 'var(--text-tertiary)' }}>
                {t('builder.panel.noRecommendations')}
              </p>
            )}

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

            {recommendations.channels.map((channel) => (
              <div
                key={channel}
                className="flex items-center gap-2 rounded-[var(--radius-lg)] px-3 py-2"
                style={{ border: '1px solid var(--nm-hairline)', background: 'var(--bg-primary)' }}
              >
                <Radio className="h-3.5 w-3.5 shrink-0" style={{ color: 'var(--text-tertiary)' }} />
                <span className="min-w-0 flex-1 truncate text-[12px]" style={{ color: 'var(--text-secondary)' }}>
                  {channel}
                </span>
                {/* The credential is the user's to paste, and it must never
                    pass through the conversation — so this hands off to the
                    Channels tab rather than collecting anything here. */}
                <Button
                  variant="outline"
                  size="sm"
                  onClick={() => requestPanel('channels')}
                  className="shrink-0"
                >
                  {t('builder.panel.setUp')}
                </Button>
              </div>
            ))}
          </section>

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
            there is nothing to roll back. Finishing clears the studio flag AND
            closes the drawer — re-requesting the open tab is MainLayout's
            toggle, and that transition is what makes the tab disappear from
            the switcher (the flag is sessionStorage, not reactive state). */}
        <Button
          size="sm"
          onClick={() => {
            closeStudio(agentId);
            requestPanel('builder');
          }}
        >
          {t('builder.panel.done')}
        </Button>
      </div>
    </div>
  );
}

function SectionLabel({ children }: { children: React.ReactNode }) {
  return (
    <span
      className="block text-[10.5px] font-medium uppercase tracking-[0.13em]"
      style={{ fontFamily: 'var(--font-mono)', color: 'var(--text-tertiary)' }}
    >
      {children}
    </span>
  );
}
