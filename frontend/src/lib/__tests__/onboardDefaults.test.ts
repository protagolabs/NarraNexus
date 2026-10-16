/**
 * refreshOnboardDefaults feeds the UI's per-source default maps from
 * GET /api/providers/catalog (`onboard_defaults`) -- the server owns the table.
 * A failed fetch (network) must leave the deterministic list-head fallback.
 */
import { afterEach, beforeEach, expect, test, vi } from 'vitest'

const { mockGetProviderCatalog } = vi.hoisted(() => ({ mockGetProviderCatalog: vi.fn() }))
vi.mock('@/lib/api', () => ({ api: { getProviderCatalog: mockGetProviderCatalog } }))

import { applyOnboardDefaults, defaultAgentModel, defaultHelperModel } from '../agentFramework'
import { refreshOnboardDefaults } from '../onboardDefaults'

const FLASH = 'deepseek-ai/DeepSeek-V4.1-Flash'
const listed = ['Qwen/Qwen3.5-Omni-Flash', FLASH]

beforeEach(() => {
  mockGetProviderCatalog.mockReset()
  applyOnboardDefaults({})
})
afterEach(() => applyOnboardDefaults({}))

test('maps are populated from the catalog response', async () => {
  mockGetProviderCatalog.mockResolvedValue({
    success: true,
    onboard_defaults: { netmind: { agent: FLASH, helper: FLASH } },
  })
  expect(defaultAgentModel('netmind', listed)).toBe('Qwen/Qwen3.5-Omni-Flash') // before

  await refreshOnboardDefaults()

  expect(defaultAgentModel('netmind', listed)).toBe(FLASH)
  expect(defaultHelperModel('netmind', 'openai', listed)).toBe(FLASH)
})

test('a failed fetch never throws and keeps the list-head fallback', async () => {
  mockGetProviderCatalog.mockRejectedValue(new Error('network'))
  await expect(refreshOnboardDefaults()).resolves.toBeUndefined()
  expect(defaultAgentModel('netmind', listed)).toBe('Qwen/Qwen3.5-Omni-Flash')
})
