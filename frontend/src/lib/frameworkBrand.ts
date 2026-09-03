/**
 * @file_name: frameworkBrand.ts
 * @author: NetMind.AI
 * @date: 2026-09-04
 * @description: The ONE place an agent framework id becomes a label and a
 * brand mark in the UI.
 *
 * The Dashboard directory and the Agent Profile page each carried their own
 * copy of this map, and the two had already drifted once (one knew
 * `nexus_power`, the other did not — one page showed "Nexus Power", the other
 * the raw `nexus_power`, and tsc said nothing). Same layer as
 * `getModelBrandIcon` and the channel brand map.
 *
 * Two behaviours callers rely on, both deliberate:
 *   - an UNKNOWN id is title-cased from the raw string, never mapped to some
 *     other brand — "unknown but present" beats "wrong";
 *   - a MISSING id (undefined) renders as '—' and the generic Bot glyph. The
 *     backend leaves it undefined for agents whose configuration is not the
 *     viewer's to see; inventing a default here would show a brand the agent
 *     may not be running on.
 *
 * The backend keeps a separate map for how the agent names its runtime in
 * its own system prompt (`model_identity.FRAMEWORK_DISPLAY_NAMES`). That is
 * prompt copy, not UI copy, and is not required to match these labels.
 */
import type { ComponentType } from 'react';
import { Bot } from 'lucide-react';
import { ClaudeBrandIcon, OpenAIBrandIcon } from '@/components/icons/ModelBrandIcons';
import { NexusPowerBrandIcon } from '@/components/icons/ChannelBrandIcons';

export type BrandIconComponent = ComponentType<{ className?: string }>;

const FRAMEWORK_LABELS: Record<string, string> = {
  claude_code: 'Claude Code',
  codex_cli: 'Codex',
  nexus_power: 'Nexus Power',
};

const FRAMEWORK_ICONS: Record<string, BrandIconComponent> = {
  claude_code: ClaudeBrandIcon,
  codex_cli: OpenAIBrandIcon,
  nexus_power: NexusPowerBrandIcon,
};

/** Human label for a framework id; '—' when unknown to the viewer. */
export function formatFramework(framework?: string | null): string {
  if (!framework) return '—';
  return (
    FRAMEWORK_LABELS[framework] ??
    framework
      .split('_')
      .filter(Boolean)
      .map((part) => part.charAt(0).toUpperCase() + part.slice(1))
      .join(' ')
  );
}

/** Brand mark for a framework id; the generic Bot glyph when unknown or missing. */
export function frameworkBrandIcon(framework?: string | null): BrandIconComponent {
  return (framework && FRAMEWORK_ICONS[framework]) || Bot;
}

/** The OpenAI mark is black-on-transparent and needs inverting in dark mode. */
export function frameworkIconInvertsInDark(icon: BrandIconComponent): boolean {
  return icon === OpenAIBrandIcon;
}
