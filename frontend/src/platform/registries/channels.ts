/**
 * @file_name: channels.ts
 * @author: Bin Liang
 * @date: 2026-09-04
 * @description: IM channel registry — the rows of the Channels section (label, icon, config component, status probe).
 *
 * Replaces the hard-coded `IM_CHANNELS` list in `IMChannelsSection`. Each
 * builtin channel registers its row with its own plugin id as owner
 * (`components/awareness/registerBuiltinChannels.ts`), so disabling
 * `builtin.channels.<x>` removes the row; a channel plugin registers its
 * own (usually with the schema-driven `GenericChannelConfig`).
 */
import type { ComponentType } from 'react';
import type { LucideIcon } from 'lucide-react';

import { Registry, type RegistryEntry } from './registry';

export interface ChannelConfigProps {
  onBindStateChange?: () => void;
}

/** Tri-state: a credential can exist but be inactive (imported from a bundle, awaiting activation). */
export type ChannelStatus = 'active' | 'inactive' | 'unbound';

export interface ChannelDef {
  /** Literal label, or an i18n key when `labelIsKey` is set. */
  label: string;
  labelIsKey?: boolean;
  icon: LucideIcon;
  component: ComponentType<ChannelConfigProps>;
  /** Bound + active / bound-but-inactive / no credential, for one agent. */
  fetchStatus: (agentId: string) => Promise<ChannelStatus>;
  /** Sort key; builtins use 10, 20, … so plugins can slot between. */
  order?: number;
}

export const CHANNELS = new Registry<ChannelDef>('ui.channels');

const sortedCache = new WeakMap<readonly RegistryEntry<ChannelDef>[], readonly RegistryEntry<ChannelDef>[]>();

/**
 * The rows in display order. Referentially stable per input array: the same
 * `entries` (e.g. the registry's cached `snapshot()`) always yields the same
 * sorted array, so callers can put the result straight into hook dependency
 * lists. A fresh array per call is what fed the 2026-10-30 credential-probe loop.
 * The result is shared between callers, hence `readonly`.
 */
export function sortedChannels(
  entries: readonly RegistryEntry<ChannelDef>[] = CHANNELS.snapshot(),
): readonly RegistryEntry<ChannelDef>[] {
  let sorted = sortedCache.get(entries);
  if (!sorted) {
    sorted = [...entries].sort((a, b) => (a.value.order ?? 100) - (b.value.order ?? 100));
    sortedCache.set(entries, sorted);
  }
  return sorted;
}
