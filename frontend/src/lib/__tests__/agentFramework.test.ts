/**
 * agentFramework — the provider↔framework compatibility predicates.
 *
 * `providerBacksFramework` is the frontend twin of backend
 * `provider_schema.framework_can_drive_provider`; `availableFrameworks` is what
 * keeps a picker from offering a dead end. The bug both exist for: a CLI
 * subscription card (Claude Code Login) bound to a nexus_power agent slot saved
 * fine and only failed in the middle of a run.
 *
 * B6 final cut (2026-09-07): `providerBacksFramework` no longer consults any
 * hardcoded table — every assertion below passes an explicit live
 * `frameworks` list (or, for the fail-closed cases, deliberately omits one).
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

// Mirrors what `GET/POST /api/providers/agent-framework`'s `frameworks[]` carries
// once a caller has fully loaded it. `protocol` is the backend's three-valued
// enum (`FrameworkMeta.protocol`): 'anthropic' | 'openai' | 'any' — nexus_power
// is sent as 'any' because it drives the provider API itself and works with
// either.
const LIVE_FRAMEWORKS = [
  { name: 'claude_code', protocol: 'anthropic' as const, oauth_source: 'claude_oauth' },
  { name: 'codex_cli', protocol: 'openai' as const, oauth_source: 'codex_oauth' },
  { name: 'nexus_power', protocol: 'any' as const, oauth_source: null },
]

describe('providerBacksFramework', () => {
  test('no live list yet → fails closed, never guesses from a hardcoded table', () => {
    expect(providerBacksFramework(CLAUDE_LOGIN, 'claude_code', undefined)).toBe(false)
    expect(providerBacksFramework(ANTHROPIC_KEY, 'nexus_power', undefined)).toBe(false)
  })

  test('a subscription card is redeemable only by its own CLI framework', () => {
    expect(providerBacksFramework(CLAUDE_LOGIN, 'claude_code', LIVE_FRAMEWORKS)).toBe(true)
    expect(providerBacksFramework(CLAUDE_SETUP_TOKEN, 'claude_code', LIVE_FRAMEWORKS)).toBe(true)
    expect(providerBacksFramework(CLAUDE_LOGIN, 'nexus_power', LIVE_FRAMEWORKS)).toBe(false)
    expect(providerBacksFramework(CLAUDE_SETUP_TOKEN, 'nexus_power', LIVE_FRAMEWORKS)).toBe(false)
    expect(providerBacksFramework(CODEX_LOGIN, 'codex_cli', LIVE_FRAMEWORKS)).toBe(true)
    expect(providerBacksFramework(CODEX_LOGIN, 'nexus_power', LIVE_FRAMEWORKS)).toBe(false)
  })

  test('API-key and bearer cards face the live list\'s protocol gate', () => {
    expect(providerBacksFramework(ANTHROPIC_KEY, 'claude_code', LIVE_FRAMEWORKS)).toBe(true)
    expect(providerBacksFramework(ANTHROPIC_KEY, 'codex_cli', LIVE_FRAMEWORKS)).toBe(false)
    expect(providerBacksFramework(NETMIND_OPENAI, 'codex_cli', LIVE_FRAMEWORKS)).toBe(true)
    expect(providerBacksFramework(NETMIND_OPENAI, 'claude_code', LIVE_FRAMEWORKS)).toBe(false)
  })

  test('protocol "any" accepts both anthropic and openai cards — read off the live value, not the framework id', () => {
    expect(providerBacksFramework(ANTHROPIC_KEY, 'nexus_power', LIVE_FRAMEWORKS)).toBe(true)
    expect(providerBacksFramework(NETMIND_OPENAI, 'nexus_power', LIVE_FRAMEWORKS)).toBe(true)
  })

  test('the "any" behavior is driven by the protocol VALUE, not a nexus_power id special-case', () => {
    // A hypothetical framework with a DIFFERENT id but protocol: 'any' behaves
    // identically to nexus_power — proves there is no id-based branch left.
    const anyById = [{ name: 'some_future_framework', protocol: 'any' as const, oauth_source: null }]
    expect(providerBacksFramework(ANTHROPIC_KEY, 'some_future_framework', anyById)).toBe(true)
    expect(providerBacksFramework(NETMIND_OPENAI, 'some_future_framework', anyById)).toBe(true)

    // Conversely, if nexus_power's live entry ever reported a SPECIFIC protocol
    // (not 'any'), it must be gated exactly like any other framework — proves
    // there is no leftover nexus_power-by-id bypass either.
    const nexusPinnedToAnthropic = [{ name: 'nexus_power', protocol: 'anthropic' as const, oauth_source: null }]
    expect(providerBacksFramework(NETMIND_OPENAI, 'nexus_power', nexusPinnedToAnthropic)).toBe(false)
  })

  describe('reads oauth_source straight off the live list — no hardcoded mirror consulted', () => {
    test('a hypothetical remap disagrees with what the old hardcoded mirror said, and the live list wins', () => {
      const remapped = [{ name: 'nexus_power', oauth_source: 'claude_oauth' }]
      expect(providerBacksFramework(CLAUDE_LOGIN, 'nexus_power', remapped)).toBe(true)
      expect(providerBacksFramework(CLAUDE_LOGIN, 'claude_code', remapped)).toBe(false)
    })

    test('an entry missing from the live list is simply not redeemable — there is no fallback left to consult', () => {
      const partial = [{ name: 'codex_cli', oauth_source: 'codex_oauth' }]
      expect(providerBacksFramework(CLAUDE_LOGIN, 'claude_code', partial)).toBe(false)
    })
  })
})

describe('availableFrameworks', () => {
  test('a wallet holding only a Claude Code Login offers only Claude Code', () => {
    const fws = availableFrameworks([CLAUDE_LOGIN], 'claude_code', LIVE_FRAMEWORKS)
    expect(fws.map((f) => f.id)).toEqual(['claude_code'])
  })

  test('adding an API-key card brings NexusPower back', () => {
    const fws = availableFrameworks(
      [CLAUDE_LOGIN, ANTHROPIC_KEY], 'claude_code', LIVE_FRAMEWORKS,
    )
    expect(fws.map((f) => f.id)).toEqual(['claude_code', 'nexus_power'])
  })

  test('an openai-only wallet drops claude_code but keeps NexusPower', () => {
    const fws = availableFrameworks([NETMIND_OPENAI], 'nexus_power', LIVE_FRAMEWORKS)
    expect(fws.map((f) => f.id)).toEqual(['codex_cli', 'nexus_power'])
  })

  test('no providers yet → no filtering (never an empty dropdown), even with no live list', () => {
    expect(availableFrameworks([], 'claude_code', undefined)).toHaveLength(
      AGENT_FRAMEWORKS.length,
    )
  })

  test('the current framework is kept even when nothing can drive it', () => {
    // A stored nexus_power pin with only a Codex login in the wallet: hiding
    // the selected value would silently re-point the <select> elsewhere.
    const fws = availableFrameworks([CODEX_LOGIN], 'nexus_power', LIVE_FRAMEWORKS)
    expect(fws.map((f) => f.id)).toEqual(['codex_cli', 'nexus_power'])
  })

  test('no live list yet → only the current framework survives (fail closed, not a hardcoded guess)', () => {
    const fws = availableFrameworks([CLAUDE_LOGIN, ANTHROPIC_KEY], 'claude_code', undefined)
    expect(fws.map((f) => f.id)).toEqual(['claude_code'])
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
