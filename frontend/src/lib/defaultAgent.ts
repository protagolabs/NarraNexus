/**
 * @file_name: defaultAgent.ts
 * @author: Bin Liang
 * @date: 2026-10-30
 * @description: Which agent to select when nothing valid is selected: the viewer's own newest one, never someone else's.
 *
 * `/api/auth/agents` lists the viewer's agents AND every public agent on the
 * platform, newest first, so `agents[0]` is whatever public agent a stranger
 * created last. Selecting it by default made every panel read an agent the
 * viewer does not own — a steady stream of 403s, which the edge monitor bans
 * an IP on. With no own agent the answer is no selection.
 */
import type { AgentInfo } from '@/types/api';

export function pickDefaultAgent(agents: readonly AgentInfo[], userId: string): string {
  if (!userId) return '';
  return agents.find((a) => a.created_by === userId)?.agent_id ?? '';
}
