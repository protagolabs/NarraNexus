/**
 * @file_name: onboardDefaults.ts
 * @description: Load the server-owned per-source onboarding default models
 * (GET /api/providers/catalog -> onboard_defaults) into lib/agentFramework's
 * recommended-model maps.
 */
import { api } from '@/lib/api'
import { applyOnboardDefaults } from '@/lib/agentFramework'

/**
 * Best-effort refresh. Never rejects: on any failure (network, old server
 * without the field, partially-mocked client) the maps keep their previous
 * content and picks fall back to the first listed model.
 */
export async function refreshOnboardDefaults(): Promise<void> {
  try {
    const res = await api.getProviderCatalog()
    if (res?.success && res.onboard_defaults) applyOnboardDefaults(res.onboard_defaults)
  } catch {
    // keep the deterministic list-head fallback
  }
}
