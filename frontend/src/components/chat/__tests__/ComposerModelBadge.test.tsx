/**
 * @file_name: ComposerModelBadge.test.tsx
 * @description: Behavior contract for the composer model chip's free-tier lock.
 *
 * The whole point of the PR this guards is: while the cloud free tier has
 * budget, the runtime pins runs to the fixed system model and ignores per-agent
 * overrides — so the chip must LOCK (read-only tag, no dropdown) instead of
 * offering a switch that silently no-ops. That lock lives in an early return
 * (`if (loaded && freeTierModel) return <span…>`) ahead of every other branch;
 * a future refactor mis-firing it reproduces the exact "model won't switch" bug.
 * The backend contract tests assert the `free_tier` JSON block — only this
 * verifies the block actually changes what the user can do.
 */
import { describe, expect, test, vi, beforeEach } from 'vitest';
import { render, screen } from '@testing-library/react';
import { ComposerModelBadge } from '../ComposerModelBadge';

vi.mock('react-i18next', () => ({
  useTranslation: () => ({ t: (k: string) => k }),
}));

vi.mock('react-router-dom', () => ({
  useNavigate: () => vi.fn(),
}));

const mockGetAgentLlmConfig = vi.fn();
const mockGetProviders = vi.fn();
vi.mock('@/lib/api', () => ({
  api: {
    getAgentLlmConfig: (...a: unknown[]) => mockGetAgentLlmConfig(...a),
    getProviders: (...a: unknown[]) => mockGetProviders(...a),
  },
}));

const EFF = {
  provider_id: 'p_nm',
  model: 'my-own-model',
  thinking: '',
  reasoning_effort: '',
  agent_framework: 'claude_code',
};

function wireConfig(freeTier: { active: boolean; model: string | null }) {
  mockGetAgentLlmConfig.mockResolvedValue({
    success: true,
    data: {
      agent_id: 'agent_x',
      slots: { agent: { inheriting: true, effective: EFF } },
      free_tier: freeTier,
    },
  });
  mockGetProviders.mockResolvedValue({
    success: true,
    data: {
      providers: {
        p_nm: {
          provider_id: 'p_nm',
          name: 'NetMind',
          source: 'netmind',
          protocol: 'anthropic',
          is_active: true,
          models: ['my-own-model', 'other-model'],
        },
      },
    },
  });
}

beforeEach(() => {
  mockGetAgentLlmConfig.mockReset();
  mockGetProviders.mockReset();
});

describe('ComposerModelBadge — free-tier lock', () => {
  test('free tier active: renders a read-only tag + system model, no switch control', async () => {
    wireConfig({ active: true, model: 'sys-agent-x' });
    render(<ComposerModelBadge agentId="agent_x" />);

    // The locked chip shows the tag + the SYSTEM model (not the user's own).
    expect(await screen.findByText('chat.model.freeTierTag')).toBeInTheDocument();
    expect(screen.getByText('sys-agent-x')).toBeInTheDocument();
    // It is a plain <span> with the lock tooltip — not an interactive control.
    expect(screen.getByTitle('chat.model.freeTierLocked')).toBeInTheDocument();
    expect(screen.queryByRole('button')).toBeNull();
    // The user's own model is NOT what's shown while locked.
    expect(screen.queryByText('my-own-model')).toBeNull();
  });

  test('free tier inactive: the switch control stays live', async () => {
    wireConfig({ active: false, model: null });
    render(<ComposerModelBadge agentId="agent_x" />);

    // Switchable state = a clickable button showing the agent's own model.
    const btn = await screen.findByRole('button');
    expect(btn).toBeInTheDocument();
    expect(screen.getByText('my-own-model')).toBeInTheDocument();
    // No free-tier lock chrome.
    expect(screen.queryByText('chat.model.freeTierTag')).toBeNull();
  });
});
