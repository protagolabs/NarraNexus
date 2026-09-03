/**
 * @file_name: guideAgent.ts
 * @author: NetMind.AI
 * @date: 2026-08-27
 * @description: Which agent in a list is the auto-provisioned guide agent —
 * the "first agent" a new user did not create themselves.
 *
 * A pure helper rather than a field on the page, because two places need the
 * same answer: [[welcomeSteps]] composition (is there an agent step at all?)
 * and [[StepAgent]] (which agent to introduce). It also has to stay right after
 * an import: agents brought in from Claude Code / Codex are never
 * bootstrap-active, so the guide is still the one that gets introduced.
 */

import type { AgentInfo } from '@/types';

export function pickGuideAgent(agents: AgentInfo[]): AgentInfo | null {
  return agents.find((a) => a.bootstrap_active) ?? agents[0] ?? null;
}
