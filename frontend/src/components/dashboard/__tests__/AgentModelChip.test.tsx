import { render, screen } from '@testing-library/react';
import { expect, test, vi } from 'vitest';
import { AgentModelChip } from '../AgentModelChip';

vi.mock('react-i18next', () => ({
  useTranslation: () => ({ t: (k: string, f?: unknown) => (typeof f === 'string' ? f : k) }),
}));

test('renders nothing without an overview entry', () => {
  const { container } = render(<AgentModelChip agentId="a1" entry={undefined} />);
  expect(container).toBeEmptyDOMElement();
});

test('shows model + "default" marker when inheriting', () => {
  render(
    <AgentModelChip
      agentId="a1"
      entry={{ agent: { model: 'V4-Pro', inheriting: true }, helper_llm: { model: 'x', inheriting: true } }}
    />,
  );
  const chip = screen.getByTestId('model-chip-a1');
  expect(chip).toHaveTextContent('V4-Pro');
  expect(chip).toHaveTextContent('default');
  expect(chip).not.toHaveTextContent('custom');
});

test('shows "custom" marker when overridden', () => {
  render(
    <AgentModelChip
      agentId="a2"
      entry={{ agent: { model: 'Claude', inheriting: false }, helper_llm: { model: 'x', inheriting: true } }}
    />,
  );
  const chip = screen.getByTestId('model-chip-a2');
  expect(chip).toHaveTextContent('Claude');
  expect(chip).toHaveTextContent('custom');
});
