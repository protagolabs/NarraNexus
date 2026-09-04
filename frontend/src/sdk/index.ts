/**
 * @file_name: index.ts
 * @author: Bin Liang
 * @date: 2026-09-03
 * @description: `@narranexus/sdk` — what a frontend plugin imports: `definePlugin`, the HostAPI types, the vite preset.
 *
 * Lives inside the app project for now so it is type-checked and tested
 * with the host; batch 6 publishes it as the `@narranexus/sdk` package
 * with the same surface. Plugins never import `@/platform/*` directly.
 */
export { definePlugin } from './definePlugin';
export { GenericChannelConfig } from '@/components/awareness/GenericChannelConfig';
export { makeGenericChannelConfig } from '@/components/awareness/genericChannelFactory';
export type { PluginDefinition } from './definePlugin';
export type { HostAPI, Disposable } from '@/platform/host';
export type { PageDef, PanelDef, CommandDef, ThemeDef, SettingsSectionDef, SidebarItemDef } from '@/platform/registries';
export type {
  ChannelConfigProps,
  ChannelDef,
  ChannelStatus,
  ConversationKindDef,
  MessageRendererDef,
  MessageRendererProps,
  SlotActionContext,
  SlotActionDef,
  SlotComponentDef,
  SlotComponentProps,
  TimelineEventDef,
  TimelineEventProps,
  WhenClause,
  WhenContext,
} from '@/platform/registries';
export { HOST_EXTERNALS, hostShimModule, vitePreset } from './vitePreset';
export { THEME_TOKENS } from '@/platform/registries/themeTokens.generated';
