/**
 * @file_name: SettingsPage.tsx
 * @author: NexusAgent
 * @date: 2026-04-02
 * @description: Settings page — reuses existing ProviderSettings + adds mode switching
 *
 * Uses the existing ProviderSettings component (which calls /api/providers)
 * for LLM configuration, and adds a mode switch section for local/cloud toggle.
 */

import { ProviderSettings } from '@/components/settings/ProviderSettings';
import { EmbeddingStatus } from '@/components/ui/EmbeddingStatus';

export default function SettingsPage() {
  return (
    <div className="h-full overflow-y-auto p-6 space-y-6">
      {/* LLM Provider Configuration — uses existing component that calls /api/providers */}
      <section>
        <h2 className="text-lg font-semibold mb-4" style={{ color: 'var(--text-primary)' }}>
          LLM Providers
        </h2>
        <ProviderSettings />
      </section>

      {/* Embedding Status */}
      <section>
        <h2 className="text-lg font-semibold mb-4" style={{ color: 'var(--text-primary)' }}>
          Embedding Index
        </h2>
        <EmbeddingStatus />
      </section>
    </div>
  );
}
