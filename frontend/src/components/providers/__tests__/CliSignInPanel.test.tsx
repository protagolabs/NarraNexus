import { describe, test, expect, vi, beforeEach } from 'vitest'
import { render, screen, waitFor, fireEvent } from '@testing-library/react'

const addProviderMock = vi.fn()
const fetchClaudeStatusMock = vi.fn()
const fetchCodexStatusMock = vi.fn()
vi.mock('../providerApi', () => ({
  addProvider: (...args: unknown[]) => addProviderMock(...args),
  fetchClaudeStatus: () => fetchClaudeStatusMock(),
  fetchCodexStatus: () => fetchCodexStatusMock(),
}))
vi.mock('@/lib/tauri', () => ({
  isTauri: () => false,
  triggerClaudeLogin: vi.fn(),
  triggerClaudeLogout: vi.fn(),
  cancelClaudeLogin: vi.fn(),
}))

import { CliSignInPanel } from '../CliSignInPanel'

beforeEach(() => {
  addProviderMock.mockReset()
  fetchClaudeStatusMock.mockReset()
  fetchCodexStatusMock.mockReset()
})

describe('CliSignInPanel', () => {
  test('web mode (no Tauri) shows the terminal fallback hint instead of a login button', async () => {
    fetchClaudeStatusMock.mockResolvedValue({ cli_installed: true, logged_in: false, email: null, expires_at: null })
    fetchCodexStatusMock.mockResolvedValue({ cli_installed: true, logged_in: false, email: null, expires_at: null })
    render(<CliSignInPanel providers={[]} onComplete={() => {}} />)
    await screen.findByText(/claude auth login/i)
  })

  test('a logged-in Claude CLI with no provider record yet shows Add as Provider, and clicking it calls addProvider', async () => {
    fetchClaudeStatusMock.mockResolvedValue({ cli_installed: true, logged_in: true, email: 'a@b.com', expires_at: null })
    fetchCodexStatusMock.mockResolvedValue({ cli_installed: false, logged_in: false, email: null, expires_at: null })
    addProviderMock.mockResolvedValue({ ok: true })
    const onComplete = vi.fn()
    render(<CliSignInPanel providers={[]} onComplete={onComplete} />)
    const button = await screen.findByText('Add as Provider')
    fireEvent.click(button)
    await waitFor(() => expect(onComplete).toHaveBeenCalled())
    expect(addProviderMock).toHaveBeenCalledWith({ card_type: 'claude_oauth' })
  })

  test('a provider record already present for claude_oauth shows the connected state, no button', async () => {
    fetchClaudeStatusMock.mockResolvedValue({ cli_installed: true, logged_in: true, email: 'a@b.com', expires_at: null })
    fetchCodexStatusMock.mockResolvedValue({ cli_installed: false, logged_in: false, email: null, expires_at: null })
    render(
      <CliSignInPanel
        providers={[{ source: 'claude_oauth', auth_type: 'oauth' }]}
        onComplete={() => {}}
      />,
    )
    await screen.findByText(/Added as a NarraNexus provider/i)
    expect(screen.queryByText('Add as Provider')).not.toBeInTheDocument()
  })

  test('pasting a Claude setup token saves it as an oauth_token provider', async () => {
    fetchClaudeStatusMock.mockResolvedValue({ cli_installed: true, logged_in: false, email: null, expires_at: null })
    fetchCodexStatusMock.mockResolvedValue({ cli_installed: false, logged_in: false, email: null, expires_at: null })
    addProviderMock.mockResolvedValue({ ok: true })
    const onComplete = vi.fn()
    render(<CliSignInPanel providers={[]} onComplete={onComplete} />)
    const input = await screen.findByPlaceholderText(/sk-ant-oat/i)
    fireEvent.change(input, { target: { value: 'sk-ant-oat-xyz' } })
    fireEvent.click(screen.getByText('Connect with token'))
    await waitFor(() => expect(onComplete).toHaveBeenCalled())
    expect(addProviderMock).toHaveBeenCalledWith({ card_type: 'claude_oauth', api_key: 'sk-ant-oat-xyz' })
  })
})
