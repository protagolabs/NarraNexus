/**
 * @file_name: onboardDefaults.ts
 * @description: Load the server-owned per-source onboarding default models
 * (GET /api/providers/catalog -> onboard_defaults) into lib/agentFramework's
 * recommended-model maps.
 */
import { api } from '@/lib/api'
import { applyOnboardDefaults } from '@/lib/agentFramework'

/**
 * Best-effort refresh. Never rejects: a failed request (network) leaves the maps
 * as they were, and picks fall back to the first listed model.
 */
export async function refreshOnboardDefaults(): Promise<void> {
  try {
    const res = await api.getProviderCatalog()
    if (res.success) applyOnboardDefaults(res.onboard_defaults)
  } catch {
    // network failure: keep the deterministic list-head fallback
  }
}
