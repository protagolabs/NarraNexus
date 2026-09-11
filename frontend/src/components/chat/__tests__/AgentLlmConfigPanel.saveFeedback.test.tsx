/**
 * Regression guard for GitHub #96: after clicking Save the dialog gave no
 * visible feedback at all — it neither closed nor showed any confirmation,
 * so a successful save looked identical to a click that did nothing. The
 * error path already worked (`error` renders); only the success path was
 * silent. ModelDefaultsSettings already has a "✓ Saved" indicator for the
 * equivalent user-level editor — this brings the per-agent editor to parity
 * with it instead of inventing a new pattern.
 */
import { describe, expect, test, vi, beforeEach } from 'vitest';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';

const setAgentLlmConfig = vi.fn();

vi.mock('@/lib/api', () => ({
  api: {
    getAgentLlmConfig: () =>
      Promise.resolve({
        success: true,
        data: {
          agent_id: 'agent_1',
          slots: {
            agent: { owner_default: { agent_framework: 'nexus_power' }, effective: null },
            helper_llm: { owner_default: {}, effective: null },
          },
        },
      }),
    getProviders: () =>
      Promise.resolve({
        success: true,
        data: {
          providers: {
            prov1: {
              provider_id: 'prov1',
              name: 'My Provider',
              source: 'user',
              protocol: 'anthropic',
              auth_type: 'api_key',
              is_active: true,
              models: ['claude-x'],
            },
          },
        },
      }),
    getAgentFramework: () =>
      Promise.resolve({
        success: true,
        data: {
          framework: 'nexus_power',
          supported: ['nexus_power', 'claude_code', 'codex_cli'],
          frameworks: [
            { name: 'nexus_power', available: true, protocol: 'any' },
            { name: 'claude_code', available: true, protocol: 'anthropic' },
            { name: 'codex_cli', available: true, protocol: 'openai' },
          ],
          probe: { ok: true, detail: '' },
        },
      }),
    setAgentLlmConfig: (...args: unknown[]) => setAgentLlmConfig(...args),
  },
}));

vi.mock('@/stores/configStore', () => ({
  useConfigStore: (sel: (s: { role: string | undefined }) => unknown) => sel({ role: 'owner' }),
}));

import { AgentLlmConfigPanel } from '../AgentLlmConfigPanel';

beforeEach(() => {
  setAgentLlmConfig.mockReset().mockResolvedValue({ success: true });
});

describe('AgentLlmConfigPanel save feedback', () => {
  test('a successful save shows a confirmation instead of silently doing nothing', async () => {
    render(
      <MemoryRouter>
        <AgentLlmConfigPanel agentId="agent_1" isOpen onClose={() => {}} />
      </MemoryRouter>,
    );

    // Pick the only provider — this also fills in the model (getModelsForSlot's
    // first entry), making the agent slot dirty and the Save button enabled.
    const providerSelect = (await screen.findAllByDisplayValue('Select provider…'))[0];
    fireEvent.change(providerSelect, { target: { value: 'prov1' } });

    const saveButton = screen.getByRole('button', { name: /^save$/i });
    await waitFor(() => expect(saveButton).not.toBeDisabled());
    fireEvent.click(saveButton);

    await waitFor(() => expect(setAgentLlmConfig).toHaveBeenCalledWith(
      'agent_1',
      'agent',
      expect.objectContaining({ provider_id: 'prov1', model: 'claude-x' }),
    ));

    const savedNote = await screen.findByText(/^✓ saved$/i);
    // Same position as ModelDefaultsSettings: after Save, so it never
    // shifts the Save button when it appears.
    expect(
      saveButton.compareDocumentPosition(savedNote) & Node.DOCUMENT_POSITION_FOLLOWING,
    ).toBeTruthy();
  });

  test('a failed save keeps showing the error, not a false confirmation', async () => {
    setAgentLlmConfig.mockResolvedValue({ success: false, detail: 'nope' });
    render(
      <MemoryRouter>
        <AgentLlmConfigPanel agentId="agent_1" isOpen onClose={() => {}} />
      </MemoryRouter>,
    );

    const providerSelect = (await screen.findAllByDisplayValue('Select provider…'))[0];
    fireEvent.change(providerSelect, { target: { value: 'prov1' } });
    const saveButton = screen.getByRole('button', { name: /^save$/i });
    await waitFor(() => expect(saveButton).not.toBeDisabled());
    fireEvent.click(saveButton);

    await screen.findByText('nope');
    expect(screen.queryByText(/^✓ saved$/i)).toBeNull();
  });

  test('helper fails after the agent slot saved → the page says the agent half landed and keeps the helper edit', async () => {
    setAgentLlmConfig
      .mockResolvedValueOnce({ success: true })
      .mockResolvedValueOnce({ success: false, detail: 'helper nope' });
    render(
      <MemoryRouter>
        <AgentLlmConfigPanel agentId="agent_1" isOpen onClose={() => {}} />
      </MemoryRouter>,
    );
    const [agentSelect, helperSelect] = await screen.findAllByDisplayValue('Select provider…');
    fireEvent.change(agentSelect, { target: { value: 'prov1' } });
    fireEvent.change(helperSelect, { target: { value: 'prov1' } });
    fireEvent.click(screen.getByRole('button', { name: /^save$/i }));

    expect(await screen.findByText(
      'The agent model was saved, but the helper model could not be saved (helper nope).',
    )).toBeInTheDocument();
    // The unsaved helper edit is still in the form.
    expect((await screen.findAllByDisplayValue('My Provider')).length).toBeGreaterThan(0);
    expect(screen.queryByText(/^✓ saved$/i)).toBeNull();
  });

  test('thinking / reasoning effort stay disabled until the agent slot has a provider', async () => {
    // Review 399d M1: on an unbound slot these knobs had nowhere to land, so
    // an edit was either refused with an unrelated message or dropped.
    render(
      <MemoryRouter>
        <AgentLlmConfigPanel agentId="agent_1" isOpen onClose={() => {}} />
      </MemoryRouter>,
    );
    const thinking = await screen.findByLabelText(/^thinking$/i);
    const effort = screen.getByLabelText(/^reasoning effort$/i);
    expect(thinking).toBeDisabled();
    expect(effort).toBeDisabled();

    const providerSelect = (await screen.findAllByDisplayValue('Select provider…'))[0];
    fireEvent.change(providerSelect, { target: { value: 'prov1' } });
    expect(thinking).not.toBeDisabled();
    expect(effort).not.toBeDisabled();
  });
});
