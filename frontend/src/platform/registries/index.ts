/**
 * @file_name: index.ts
 * @author: Bin Liang
 * @date: 2026-09-03
 * @description: Public entry of the frontend registries (the only import a plugin bundle needs).
 */
export { Registry, RegistryConflictError } from './registry';
export type { RegistryEntry, RegisterOptions } from './registry';
export { useRegistryEntries } from './hooks';
export { PAGES } from './pages';
export type { PageComponent, PageDef, PageGuard, PageLayout } from './pages';
export { SIDEBAR, sortedSidebarItems } from './sidebar';
export type { SidebarItemDef, SidebarFeatures, SidebarLocation } from './sidebar';
export { PANELS } from './panels';
export type { PanelDef, PanelProps } from './panels';
export { SETTINGS_SECTIONS, sortedSettingsSections } from './settingsSections';
export type { SettingsSectionDef, SettingsSectionProps } from './settingsSections';
export { THEMES, applyTheme, clearTheme, validateThemeTokens } from './themes';
export type { ThemeDef } from './themes';
export { COMMANDS } from './commands';
export type { CommandDef } from './commands';
export { evaluateWhen, parseWhen } from './when';
export type { WhenClause, WhenContext } from './when';
export {
  AGENT_CARD_BADGES,
  CHAT_HEADER_ACTIONS,
  COMPOSER_EXTENSIONS,
  CONVERSATION_KINDS,
  MESSAGE_ACTIONS,
  MESSAGE_RENDERERS,
  SIDEBAR_SECTIONS,
  TIMELINE_EVENTS,
  TOP_BAR_ITEMS,
  rendererFor,
  visibleSlotEntries,
} from './slotPoints';
export type {
  ConversationKindDef,
  MessageRendererDef,
  MessageRendererProps,
  SlotActionContext,
  SlotActionDef,
  SlotComponentDef,
  SlotComponentProps,
  SlotEntryBase,
  TimelineEventDef,
  TimelineEventProps,
} from './slotPoints';
