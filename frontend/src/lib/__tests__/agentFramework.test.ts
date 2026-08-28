/**
 * agentFramework — the provider↔framework compatibility predicates.
 *
 * `providerBacksFramework` is the frontend twin of backend
 * `provider_schema.framework_can_drive_provider`; `availableFrameworks` is what
 * keeps a picker from offering a dead end. The bug both exist for: a CLI
 * subscription card (Claude Code Login) bound to a nexus_power agent slot saved
 * fine and only failed in the middle of a run.
 */
import { describe, test, expect } from 'vitest'
import {
  availableFrameworks,
  providerBacksFramework,
  AGENT_FRAMEWORKS,
  frameworkAvailabilityMap,
  withFrameworkAvailability,
  isFrameworkAvailable,
} from '../agentFramework'

const card = (source: string, protocol: string, auth_type: string) => ({
  source,
  protocol,
  auth_type,
})

const CLAUDE_LOGIN = card('claude_oauth', 'anthropic', 'oauth')
const CLAUDE_SETUP_TOKEN = card('claude_oauth', 'anthropic', 'oauth_token')
const CODEX_LOGIN = card('codex_oauth', 'openai', 'oauth')
const ANTHROPIC_KEY = card('user', 'anthropic', 'api_key')
const NETMIND_OPENAI = card('netmind', 'openai', 'bearer_token')

describe('providerBacksFramework', () => {
  test('a subscription card is redeemable only by its own CLI framework', () => {
    expect(providerBacksFramework(CLAUDE_LOGIN, 'claude_code')).toBe(true)
    expect(providerBacksFramework(CLAUDE_SETUP_TOKEN, 'claude_code')).toBe(true)
    expect(providerBacksFramework(CLAUDE_LOGIN, 'nexus_power')).toBe(false)
    expect(providerBacksFramework(CLAUDE_SETUP_TOKEN, 'nexus_power')).toBe(false)
    expect(providerBacksFramework(CODEX_LOGIN, 'codex_cli')).toBe(true)
    expect(providerBacksFramework(CODEX_LOGIN, 'nexus_power')).toBe(false)
  })

  test('API-key and bearer cards only face the protocol gate', () => {
    expect(providerBacksFramework(ANTHROPIC_KEY, 'nexus_power')).toBe(true)
    expect(providerBacksFramework(ANTHROPIC_KEY, 'claude_code')).toBe(true)
    expect(providerBacksFramework(ANTHROPIC_KEY, 'codex_cli')).toBe(false)
    expect(providerBacksFramework(NETMIND_OPENAI, 'nexus_power')).toBe(true)
    expect(providerBacksFramework(NETMIND_OPENAI, 'codex_cli')).toBe(true)
    expect(providerBacksFramework(NETMIND_OPENAI, 'claude_code')).toBe(false)
  })
})

describe('availableFrameworks', () => {
  test('a wallet holding only a Claude Code Login offers only Claude Code', () => {
    const fws = availableFrameworks([CLAUDE_LOGIN], 'claude_code')
    expect(fws.map((f) => f.id)).toEqual(['claude_code'])
  })

  test('adding an API-key card brings NexusPower back', () => {
    const fws = availableFrameworks(
      [CLAUDE_LOGIN, ANTHROPIC_KEY], 'claude_code',
    )
    expect(fws.map((f) => f.id)).toEqual(['claude_code', 'nexus_power'])
  })

  test('an openai-only wallet drops claude_code', () => {
    const fws = availableFrameworks([NETMIND_OPENAI], 'nexus_power')
    expect(fws.map((f) => f.id)).toEqual(['codex_cli', 'nexus_power'])
  })

  test('no providers yet → no filtering (never an empty dropdown)', () => {
    expect(availableFrameworks([], 'claude_code')).toHaveLength(
      AGENT_FRAMEWORKS.length,
    )
  })

  test('the current framework is kept even when nothing can drive it', () => {
    // A stored nexus_power pin with only a Codex login in the wallet: hiding
    // the selected value would silently re-point the <select> elsewhere.
    const fws = availableFrameworks([CODEX_LOGIN], 'nexus_power')
    expect(fws.map((f) => f.id)).toEqual(['codex_cli', 'nexus_power'])
  })
})

describe('plugin-install availability (frameworkAvailabilityMap / withFrameworkAvailability)', () => {
  test('an uninstalled plugin is marked unavailable, never hidden', () => {
    const map = frameworkAvailabilityMap([
      { name: 'claude_code', available: true },
      { name: 'codex_cli', available: false },
    ])
    const merged = withFrameworkAvailability(AGENT_FRAMEWORKS, map)
    // Still every framework — plugin gating disables, it does not filter.
    expect(merged.map((f) => f.id)).toEqual(AGENT_FRAMEWORKS.map((f) => f.id))
    expect(isFrameworkAvailable(merged.find((f) => f.id === 'claude_code')!)).toBe(true)
    expect(isFrameworkAvailable(merged.find((f) => f.id === 'codex_cli')!)).toBe(false)
  })

  test('a framework the backend never mentioned defaults to available', () => {
    // nexus_power isn't a plugin (no install step) — an older or partial
    // backend response with no entry for it must not lock it out.
    const map = frameworkAvailabilityMap([{ name: 'codex_cli', available: false }])
    const merged = withFrameworkAvailability(AGENT_FRAMEWORKS, map)
    expect(isFrameworkAvailable(merged.find((f) => f.id === 'nexus_power')!)).toBe(true)
  })

  test('an entirely absent frameworks array (older backend) leaves everything available', () => {
    const map = frameworkAvailabilityMap(undefined)
    const merged = withFrameworkAvailability(AGENT_FRAMEWORKS, map)
    expect(merged.every((f) => isFrameworkAvailable(f))).toBe(true)
  })
})
