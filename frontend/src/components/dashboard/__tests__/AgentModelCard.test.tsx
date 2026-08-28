import { render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, expect, test, vi } from 'vitest';
import { AgentModelCard } from '../AgentModelCard';

vi.mock('react-i18next', () => ({
  useTranslation: () => ({ t: (k: string, f?: unknown) => (typeof f === 'string' ? f : k) }),
}));

const mockGetAgentLlmConfig = vi.fn();
vi.mock('@/lib/api', () => ({
  api: { getAgentLlmConfig: (...a: unknown[]) => mockGetAgentLlmConfig(...a) },
}));

beforeEach(() => {
  mockGetAgentLlmConfig.mockReset().mockResolvedValue({
    success: true,
    data: {
      agent_id: 'a1',
      slots: {
        agent: {
          inheriting: true,
          effective: { model: 'V4-Pro', agent_framework: 'nexus_power', reasoning_effort: 'high' },
          override: null,
          owner_default: null,
        },
        helper_llm: {
          inheriting: false,
          effective: { model: 'V4-Flash', reasoning_effort: 'low' },
          override: null,
          owner_default: null,
        },
      },
    },
  });
});
afterEach(() => vi.restoreAllMocks());

test('renders effective models with inherit/override badges', async () => {
  render(<AgentModelCard agentId="a1" reloadKey={0} onEdit={vi.fn()} />);
  await waitFor(() => expect(screen.getByText('V4-Pro')).toBeInTheDocument());
  expect(screen.getByText('V4-Flash')).toBeInTheDocument();
  expect(screen.getByTestId('agent-slot-inherit')).toBeInTheDocument();
  expect(screen.getByTestId('helper_llm-slot-override')).toBeInTheDocument();
});

test('edit button fires onEdit', async () => {
  const onEdit = vi.fn();
  render(<AgentModelCard agentId="a1" reloadKey={0} onEdit={onEdit} />);
  await waitFor(() => screen.getByText('V4-Pro'));
  screen.getByTestId('agent-model-edit').click();
  expect(onEdit).toHaveBeenCalled();
});
