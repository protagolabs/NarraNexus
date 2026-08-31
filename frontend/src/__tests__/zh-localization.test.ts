/**
 * @file_name: zh-localization.test.ts
 * @description: Regression coverage for Chinese localization of first-run and model reasoning UI.
 */

import { readFileSync } from 'node:fs';
import { describe, expect, it } from 'vitest';
import en from '@/i18n/locales/en.json';
import zh from '@/i18n/locales/zh.json';

const source = (path: string) => readFileSync(new URL(path, import.meta.url), 'utf8');

describe('Chinese localization completeness', () => {
  it('defines Chinese copy for onboarding, setup, greeting, and reasoning controls', () => {
    expect(zh.onboarding.guideCoachmark.text).not.toBe(en.onboarding.guideCoachmark.text);
    expect(zh.pages.setup.welcome).not.toBe(en.pages.setup.welcome);
    expect(zh.chat.bootstrapGreeting).not.toBe(en.chat.bootstrapGreeting);
    expect(zh.chat.securityReminder).not.toBe(en.chat.securityReminder);
    expect(zh.chat.execution.selectingNarrative).not.toBe(en.chat.execution.selectingNarrative);
    expect(zh.pages.settings.modelDefaults.reasoningEffort).not.toBe(
      en.pages.settings.modelDefaults.reasoningEffort,
    );
  });

  it('defines Chinese copy for the privacy pane and telemetry disclosure', () => {
    expect(zh.pages.settings.nav.privacy).not.toBe(en.pages.settings.nav.privacy);
    expect(zh.pages.settings.privacy.telemetryDesc).not.toBe(
      en.pages.settings.privacy.telemetryDesc,
    );
    expect(zh.telemetryNotice.body).not.toBe(en.telemetryNotice.body);
  });

  it('routes the affected frontend surfaces through i18n', () => {
    const agentConfig = source('../components/chat/AgentLlmConfigPanel.tsx');
    const modelDefaults = source('../components/settings/ModelDefaultsSettings.tsx');
    const chatPanel = source('../components/chat/ChatPanel.tsx');
    const settingsPage = source('../pages/SettingsPage.tsx');
    const oneKeyOnboard = source('../components/settings/OneKeyOnboard.tsx');
    const providerSettings = source('../components/settings/ProviderSettings.tsx');

    expect(source('../components/onboarding/GuideAgentCoachmark.tsx')).toContain(
      "t('onboarding.guideCoachmark.text')",
    );
    expect(source('../pages/SetupPage.tsx')).toContain("t('pages.setup.welcome')");
    expect(settingsPage).toContain("t('pages.settings.title')");
    expect(settingsPage).toContain("t(item.labelKey)");
    expect(settingsPage).not.toContain('>Settings<');
    const privacySettings = source('../components/settings/PrivacySettings.tsx');
    expect(privacySettings).toContain("t('pages.settings.privacy.telemetryTitle')");
    expect(privacySettings).not.toContain('>Diagnostic telemetry<');
    const telemetryNotice = source('../components/telemetry/TelemetryNotice.tsx');
    expect(telemetryNotice).toContain("t('telemetryNotice.body')");
    expect(telemetryNotice).not.toContain('>Got it<');
    expect(settingsPage).not.toContain("label: 'Model Defaults'");
    expect(oneKeyOnboard).toContain("t('settings.provider.oneKeyTitle')");
    expect(oneKeyOnboard).not.toContain('>One key to start<');
    expect(oneKeyOnboard).not.toContain('Setting up...');
    // The login cards moved to SubscriptionConnect (2026-08-28) — the
    // guards move WITH them, or they assert against a file that no
    // longer contains the strings and pass vacuously. The negative
    // checks on ProviderSettings stay: they now also guard against the
    // copy drifting back.
    expect(providerSettings).not.toContain('Checking status...');
    expect(providerSettings).not.toContain('>Claude Code Login<');
    expect(providerSettings).not.toContain('>Codex CLI Login<');
    const subscriptionConnect = source('../components/settings/SubscriptionConnect.tsx');
    expect(subscriptionConnect).not.toContain('Checking status...');
    expect(subscriptionConnect).not.toContain('>Claude Code Login<');
    expect(subscriptionConnect).not.toContain('>Codex CLI Login<');
    expect(subscriptionConnect).toContain("t('settings.provider.claudeLoginTitle')");
    expect(subscriptionConnect).toContain("t('settings.provider.codexLoginTitle')");
    expect(chatPanel).toContain("t('chat.bootstrapGreeting')");
    expect(chatPanel).toContain("t('chat.securityReminder')");
    expect(chatPanel).not.toContain('Security reminder: never paste sensitive');
    expect(chatPanel).toContain('localizeBootstrapGreeting(item.content)');
    // The pipeline-phase labels moved from ChatPanel's message-area
    // indicator into the run preamble (2026-07-30, reframed 2026-08-31),
    // whose phase-label map lives in the shared render pieces — assert there.
    const processShared = source('../components/chat/process/processShared.tsx');
    expect(processShared).toContain("'chat.execution.selectingNarrative'");
    expect(chatPanel).not.toContain("return 'Loading context...'");
    expect(agentConfig).toContain(
      "t('pages.settings.modelDefaults.reasoningEffort')",
    );
    expect(modelDefaults).toContain(
      "t('pages.settings.modelDefaults.reasoningEffort')",
    );

    for (const literal of [
      'Agent model & framework',
      'Agent (main dialogue)',
      'Helper LLM (background tasks)',
      'Select provider…',
      'Select model…',
      'Reset this slot to the global default',
    ]) {
      expect(agentConfig).not.toContain(`>${literal}<`);
      expect(modelDefaults).not.toContain(`>${literal}<`);
    }
  });
});
