import { describe, test, expect, vi, beforeEach } from 'vitest'
import { render, screen, fireEvent, waitFor } from '@testing-library/react'

const addProviderMock = vi.fn()
const testProviderConfigMock = vi.fn()
vi.mock('../providerApi', () => ({
  addProvider: (...args: unknown[]) => addProviderMock(...args),
  testProviderConfig: (...args: unknown[]) => testProviderConfigMock(...args),
}))

import { CustomEndpointForm } from '../CustomEndpointForm'

beforeEach(() => {
  addProviderMock.mockReset()
  testProviderConfigMock.mockReset()
})

describe('CustomEndpointForm', () => {
  test('picking a protocol reveals the endpoint fields with a prefilled base URL', () => {
    render(<CustomEndpointForm onComplete={() => {}} />)
    fireEvent.change(screen.getByRole('combobox', { name: /protocol/i }), {
      target: { value: 'anthropic' },
    })
    expect(screen.getByDisplayValue('https://api.anthropic.com')).toBeInTheDocument()
  })

  test('submitting calls addProvider with the form fields and fires onComplete on success', async () => {
    addProviderMock.mockResolvedValue({ ok: true })
    const onComplete = vi.fn()
    render(<CustomEndpointForm onComplete={onComplete} />)
    fireEvent.change(screen.getByRole('combobox', { name: /protocol/i }), {
      target: { value: 'openai' },
    })
    fireEvent.change(screen.getByPlaceholderText('Your API key'), {
      target: { value: 'sk-custom-1' },
    })
    fireEvent.click(screen.getByText('Add Provider'))
    await waitFor(() => expect(onComplete).toHaveBeenCalled())
    expect(addProviderMock).toHaveBeenCalledWith(
      expect.objectContaining({ card_type: 'openai', api_key: 'sk-custom-1' }),
    )
  })

  test('a failed submit shows the backend detail and does not fire onComplete', async () => {
    addProviderMock.mockResolvedValue({ ok: false, detail: 'endpoint unreachable' })
    const onComplete = vi.fn()
    render(<CustomEndpointForm onComplete={onComplete} />)
    fireEvent.change(screen.getByRole('combobox', { name: /protocol/i }), {
      target: { value: 'openai' },
    })
    fireEvent.change(screen.getByPlaceholderText('Your API key'), {
      target: { value: 'sk-custom-1' },
    })
    fireEvent.click(screen.getByText('Add Provider'))
    await screen.findByText('endpoint unreachable')
    expect(onComplete).not.toHaveBeenCalled()
  })

  test('test connection reports the probe result without submitting', async () => {
    testProviderConfigMock.mockResolvedValue({ ok: true, msg: 'reachable' })
    render(<CustomEndpointForm onComplete={() => {}} />)
    fireEvent.change(screen.getByRole('combobox', { name: /protocol/i }), {
      target: { value: 'anthropic' },
    })
    fireEvent.change(screen.getByPlaceholderText('Your API key'), {
      target: { value: 'sk-ant-1' },
    })
    fireEvent.click(screen.getByText('Test connection'))
    await screen.findByText('reachable')
    expect(addProviderMock).not.toHaveBeenCalled()
  })
})
