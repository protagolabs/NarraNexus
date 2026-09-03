/**
 * @file_name: registerBuiltinSections.ts
 * @author: Bin Liang
 * @date: 2026-09-03
 * @description: The shell's settings sections registered into SETTINGS_SECTIONS, from the settings chunk.
 *
 * Registered here rather than in `platform/builtin.ts` so the section
 * components stay in the (lazy) settings chunk and render synchronously
 * once the page is open — exactly the bundle shape and behavior the page
 * had before the registry. Plugins register their sections from their own
 * bundles; order values interleave.
 */
import { Cpu, Download, FolderArchive, Palette, Puzzle, Shield, SlidersHorizontal, User } from 'lucide-react';

import { SETTINGS_SECTIONS } from '@/platform/registries';
import {
  AccountSection,
  ArtifactsContent,
  ModelDefaultsSection,
  PersonalizationSection,
  PluginsSection,
  PrivacySection,
  ProvidersSection,
  UpdatesSectionGuarded,
} from './sections';

const OWNER = { owner: 'builtin.ui' };

if (!SETTINGS_SECTIONS.has('providers')) {
  SETTINGS_SECTIONS.register('account', { labelKey: 'pages.settings.nav.account', icon: User, component: AccountSection, order: 10, neverDefault: true }, OWNER);
  SETTINGS_SECTIONS.register('providers', { labelKey: 'pages.settings.nav.providers', icon: Cpu, component: ProvidersSection, order: 20 }, OWNER);
  SETTINGS_SECTIONS.register('modeldefaults', { labelKey: 'pages.settings.nav.modelDefaults', icon: SlidersHorizontal, component: ModelDefaultsSection, order: 30 }, OWNER);
  // Plugins are a LOCAL-only concept (cloud pre-installs the frameworks in the image).
  SETTINGS_SECTIONS.register('plugins', { labelKey: 'pages.settings.nav.plugins', icon: Puzzle, component: PluginsSection, order: 40, cloudHidden: true }, OWNER);
  SETTINGS_SECTIONS.register('artifacts', { labelKey: 'pages.settings.nav.artifacts', icon: FolderArchive, component: ArtifactsContent, order: 50 }, OWNER);
  SETTINGS_SECTIONS.register('privacy', { labelKey: 'pages.settings.nav.privacy', icon: Shield, component: PrivacySection, order: 60 }, OWNER);
  SETTINGS_SECTIONS.register('personalization', { labelKey: 'pages.settings.nav.personalization', icon: Palette, component: PersonalizationSection, order: 70 }, OWNER);
  SETTINGS_SECTIONS.register('updates', { labelKey: 'pages.settings.nav.updates', icon: Download, component: UpdatesSectionGuarded, order: 80, desktopOnly: true }, OWNER);
}
