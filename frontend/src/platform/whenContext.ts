/**
 * @file_name: whenContext.ts
 * @author: Bin Liang
 * @date: 2026-09-04
 * @description: Build the `WhenContext` slot predicates are evaluated against, from the host's stores.
 *
 * `agentHas:<module>` reads the agent's module instances (the agents list
 * carries `modules` when the backend reports them); `setting:<key>` reads
 * the config store's flat keys (e.g. `narrationTier`, `fastMode`) — the
 * host decides what a setting key means, a plugin only names it.
 */
import { useMemo } from 'react';

import { useConfigStore } from '@/stores';

import type { WhenContext } from './registries/when';

export function useWhenContext(input: { conversationKind?: string; agentId?: string | null }): WhenContext {
  const agents = useConfigStore((s) => s.agents);
  const settings = useConfigStore((s) => s as unknown as Record<string, unknown>);
  return useMemo(() => {
    const agent = input.agentId ? agents.find((a) => a.agent_id === input.agentId) : undefined;
    const modules = (agent as { modules?: readonly string[] } | undefined)?.modules ?? [];
    return { conversationKind: input.conversationKind, agentModules: modules, settings };
  }, [agents, settings, input.agentId, input.conversationKind]);
}
