import { describe, test, expect, vi, beforeEach, afterEach } from 'vitest'

vi.mock('@/stores/runtimeStore', () => ({
  getApiBaseUrl: () => 'https://api.test',
}))

import { authFetch, providerUrl, addProvider, testProviderConfig, fetchClaudeStatus, fetchCodexStatus } from '../providerApi'

const originalFetch = global.fetch

beforeEach(() => {
  global.fetch = vi.fn()
  localStorage.clear()
})

afterEach(() => {
  global.fetch = originalFetch
})

describe('providerUrl', () => {
  test('builds the providers endpoint off the current API base', () => {
    expect(providerUrl()).toBe('https://api.test/api/providers')
    expect(providerUrl('/claude-status')).toBe('https://api.test/api/providers/claude-status')
  })
})

describe('authFetch', () => {
  test('injects Authorization and X-User-Id from localStorage config', async () => {
    localStorage.setItem(
      'narra-nexus-config',
      JSON.stringify({ state: { token: 'jwt-123', userId: 'user-1' } }),
    )
    ;(global.fetch as ReturnType<typeof vi.fn>).mockResolvedValue(new Response('{}'))
    await authFetch('https://api.test/x')
    const [, init] = (global.fetch as ReturnType<typeof vi.fn>).mock.calls[0]
    const headers = init.headers as Headers
    expect(headers.get('Authorization')).toBe('Bearer jwt-123')
    expect(headers.get('X-User-Id')).toBe('user-1')
  })

  test('proceeds without headers when localStorage config is absent', async () => {
    ;(global.fetch as ReturnType<typeof vi.fn>).mockResolvedValue(new Response('{}'))
    await authFetch('https://api.test/x')
    const [, init] = (global.fetch as ReturnType<typeof vi.fn>).mock.calls[0]
    const headers = init.headers as Headers
    expect(headers.get('Authorization')).toBeNull()
  })
})

describe('addProvider', () => {
  test('returns ok:true and does not throw on a successful response', async () => {
    ;(global.fetch as ReturnType<typeof vi.fn>).mockResolvedValue(
      new Response(JSON.stringify({ success: true }), { status: 200 }),
    )
    const result = await addProvider({ card_type: 'anthropic', api_key: 'sk-x' })
    expect(result).toEqual({ ok: true })
  })

  test('returns ok:false with the backend detail on failure', async () => {
    ;(global.fetch as ReturnType<typeof vi.fn>).mockResolvedValue(
      new Response(JSON.stringify({ success: false, detail: 'bad key' }), { status: 200 }),
    )
    const result = await addProvider({ card_type: 'anthropic', api_key: 'sk-x' })
    expect(result).toEqual({ ok: false, detail: 'bad key' })
  })

  test('returns ok:false on a network error, without throwing', async () => {
    ;(global.fetch as ReturnType<typeof vi.fn>).mockRejectedValue(new Error('network down'))
    const result = await addProvider({ card_type: 'anthropic' })
    expect(result.ok).toBe(false)
  })
})

describe('testProviderConfig', () => {
  test('surfaces the backend message on a probe failure', async () => {
    ;(global.fetch as ReturnType<typeof vi.fn>).mockResolvedValue(
      new Response(JSON.stringify({ success: false, message: 'unauthorized' }), { status: 200 }),
    )
    const result = await testProviderConfig({ card_type: 'anthropic', api_key: 'sk-bad' })
    expect(result).toEqual({ ok: false, msg: 'unauthorized' })
  })

  test('returns ok:true with undefined msg on a successful probe', async () => {
    ;(global.fetch as ReturnType<typeof vi.fn>).mockResolvedValue(
      new Response(JSON.stringify({ success: true }), { status: 200 }),
    )
    const result = await testProviderConfig({ card_type: 'anthropic', api_key: 'sk-x' })
    expect(result).toEqual({ ok: true, msg: undefined })
  })

  test('returns ok:false on a network error, without throwing', async () => {
    ;(global.fetch as ReturnType<typeof vi.fn>).mockRejectedValue(new Error('network down'))
    const result = await testProviderConfig({ card_type: 'anthropic', api_key: 'sk-x' })
    expect(result.ok).toBe(false)
  })
})

describe('fetchClaudeStatus', () => {
  test('resolves to the data object on a successful response', async () => {
    const mockData = { cli_installed: true, logged_in: true, email: 'a@b.com', expires_at: null }
    ;(global.fetch as ReturnType<typeof vi.fn>).mockResolvedValue(
      new Response(JSON.stringify({ success: true, data: mockData }), { status: 200 }),
    )
    const result = await fetchClaudeStatus()
    expect(result).toEqual(mockData)
  })

  test('resolves to null when success is false', async () => {
    ;(global.fetch as ReturnType<typeof vi.fn>).mockResolvedValue(
      new Response(JSON.stringify({ success: false }), { status: 200 }),
    )
    const result = await fetchClaudeStatus()
    expect(result).toBeNull()
  })

  test('resolves to null on a network error, without throwing', async () => {
    ;(global.fetch as ReturnType<typeof vi.fn>).mockRejectedValue(new Error('network down'))
    const result = await fetchClaudeStatus()
    expect(result).toBeNull()
  })
})

describe('fetchCodexStatus', () => {
  test('resolves to the data object on a successful response', async () => {
    const mockData = { cli_installed: true, logged_in: true, email: 'a@b.com', expires_at: null }
    ;(global.fetch as ReturnType<typeof vi.fn>).mockResolvedValue(
      new Response(JSON.stringify({ success: true, data: mockData }), { status: 200 }),
    )
    const result = await fetchCodexStatus()
    expect(result).toEqual(mockData)
  })

  test('resolves to null when success is false', async () => {
    ;(global.fetch as ReturnType<typeof vi.fn>).mockResolvedValue(
      new Response(JSON.stringify({ success: false }), { status: 200 }),
    )
    const result = await fetchCodexStatus()
    expect(result).toBeNull()
  })

  test('resolves to null on a network error, without throwing', async () => {
    ;(global.fetch as ReturnType<typeof vi.fn>).mockRejectedValue(new Error('network down'))
    const result = await fetchCodexStatus()
    expect(result).toBeNull()
  })
})
