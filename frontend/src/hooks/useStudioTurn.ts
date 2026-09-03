/**
 * @file_name: useStudioTurn.ts
 * @author: NetMind.AI
 * @date: 2026-09-03
 * @description: The creation studio's two hooks into the ordinary chat turn.
 *
 * ChatPanel is ~1400 lines of load-bearing streaming logic, so the studio
 * touches it through exactly two calls — `encodeOutgoing` before a send, and
 * `applyFromReply` when a turn settles. Everything else lives here.
 *
 * Why the instruction rides on EVERY turn rather than just the first: the
 * envelope carries the agent's current configuration, and that is what makes
 * the user's own panel edits authoritative — the model revises what the panel
 * holds instead of overwriting it from memory. It also keeps a weaker model
 * emitting the draft block, which a single turn-1 instruction reliably fails
 * to do after a few exchanges (binding rule #15: the platform does not police
 * the user's model choice, so it has to tolerate one).
 *
 * The skill catalogue is fetched once per mount and only when the studio is
 * open — it exists solely so `mergeAgentDraft` can reject ids that do not
 * exist, and so the panel can install a recommendation by id.
 */
import { useCallback, useEffect, useRef, useState } from 'react';
import { api } from '@/lib/api';
import { useConfigStore, usePreloadStore } from '@/stores';
import {
  encodeBuilderTurn,
  mergeAgentDraft,
  parseAgentDraft,
  type SkillOption,
} from '@/lib/builderProtocol';
import { applyLiveFields, readCurrentConfig } from '@/lib/builderApply';
import { isStudioOpen, readRecommendations, saveRecommendations } from '@/lib/builderSession';

const CATALOGUE_LIMIT = 60;

export function useStudioTurn(agentId: string | null) {
  const agents = useConfigStore((s) => s.agents);
  const refreshAgents = useConfigStore((s) => s.refreshAgents);
  const refreshAwareness = usePreloadStore((s) => s.refreshAwareness);

  const [catalogue, setCatalogue] = useState<SkillOption[]>([]);
  // Read inside callbacks without making them re-created per fetch.
  const catalogueRef = useRef<SkillOption[]>([]);
  const [error, setError] = useState<string | null>(null);

  const studioOpen = isStudioOpen(agentId);

  useEffect(() => {
    if (!studioOpen) return;
    let cancelled = false;
    api
      .searchMarketplaceSkills({ limit: CATALOGUE_LIMIT })
      .then((res) => {
        if (cancelled) return;
        const next = (res.items ?? []).map((s) => ({
          id: s.skill_id,
          name: s.name,
          description: s.description ?? '',
        }));
        catalogueRef.current = next;
        setCatalogue(next);
      })
      .catch(() => {
        // No catalogue means the merge rejects every proposed skill id, which
        // is the safe direction: recommendations vanish, text fields still work.
        if (!cancelled) catalogueRef.current = [];
      });
    return () => {
      cancelled = true;
    };
  }, [studioOpen]);

  const identity = useCallback(() => {
    const found = agents.find((a) => a.agent_id === agentId);
    return { name: found?.name ?? '', description: found?.description ?? '' };
  }, [agents, agentId]);

  /**
   * Wrap an outgoing message, or return it untouched when the studio is shut.
   * Returns the original text on any failure — a studio hiccup must never
   * swallow the message the user just wrote.
   */
  const encodeOutgoing = useCallback(
    async (request: string): Promise<string> => {
      if (!agentId || !isStudioOpen(agentId)) return request;
      try {
        const current = await readCurrentConfig(
          agentId,
          identity(),
          readRecommendations(agentId),
        );
        return (
          encodeBuilderTurn({
            request,
            current,
            availableSkills: catalogueRef.current,
          }) ?? request
        );
      } catch {
        return request;
      }
    },
    [agentId, identity],
  );

  /**
   * Apply a settled reply's draft block to the agent.
   *
   * Text fields are written; skills and channels are stored as
   * recommendations for the panel to offer. A parse failure is a no-op by
   * design: "this turn changed no configuration" is a perfectly good outcome
   * and must never interrupt the conversation.
   */
  const applyFromReply = useCallback(
    async (replyText: string): Promise<void> => {
      if (!agentId || !isStudioOpen(agentId)) return;
      const parsed = parseAgentDraft(replyText);
      if (!parsed) return;
      try {
        const prev = await readCurrentConfig(agentId, identity(), readRecommendations(agentId));
        const next = mergeAgentDraft(
          prev,
          parsed,
          catalogueRef.current.map((s) => s.id),
        );
        const outcome = await applyLiveFields(agentId, prev, next);
        saveRecommendations(agentId, {
          skill_ids: next.skill_ids,
          channels: next.channels,
        });
        setError(outcome.errors.length ? outcome.errors.join('; ') : null);
        // Refresh only what actually changed, so a turn that touched one field
        // does not refetch the other.
        if (outcome.changed.includes('identity')) void refreshAgents();
        if (outcome.changed.includes('awareness')) void refreshAwareness(agentId, true);
      } catch (e) {
        setError(String(e));
      }
    },
    [agentId, identity, refreshAgents, refreshAwareness],
  );

  return { studioOpen, catalogue, error, encodeOutgoing, applyFromReply };
}
