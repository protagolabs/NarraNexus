/**
 * @file_name: BrowserApprovalPrompt.test.tsx
 * @description: Contract for the site-approval prompt.
 *
 * These assertions are about not training people to click yes. A prompt that
 * says "a website wants access", or that can be dismissed with Enter, or
 * whose loudest button is the broadest grant, produces consent that was never
 * actually given.
 */
import { expect, test, vi } from 'vitest';
import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';

import BrowserApprovalPrompt from '../BrowserApprovalPrompt';
import type { PendingApproval } from '@/types/browser';

const approval: PendingApproval = {
  id: 'appr_1', agent_id: 'a1', origin: 'https://www.baidu.com',
  capability: 'downloads', requested_at: 0,
  allowed_lifetimes: ['thread', 'always'],
};

function renderPrompt(over: Partial<PendingApproval> = {}) {
  const onDecide = vi.fn(async () => {});
  render(<BrowserApprovalPrompt approval={{ ...approval, ...over }} onDecide={onDecide} />);
  return onDecide;
}

test('the exact origin is shown, whole', () => {
  renderPrompt();
  expect(screen.getByTestId('browser-approval-origin').textContent).toContain('https://www.baidu.com');
});

test('a lookalike host is not hidden by truncation', () => {
  renderPrompt({ origin: 'https://accounts.google.com.evil.example' });
  expect(screen.getByTestId('browser-approval-origin').textContent)
    .toContain('accounts.google.com.evil.example');
});

test('the capability being asked for is named', () => {
  renderPrompt({ capability: 'uploads' });
  expect(screen.getByTestId('browser-approval').textContent).toMatch(/upload files to/i);
});

test('"allow for this conversation" grants only the thread', () => {
  const onDecide = renderPrompt();
  fireEvent.click(screen.getByTestId('browser-approval-allow-thread'));
  expect(onDecide).toHaveBeenCalledWith('allow', 'thread');
});

test('"always" is a separate, deliberate choice', () => {
  const onDecide = renderPrompt();
  fireEvent.click(screen.getByTestId('browser-approval-allow-always'));
  expect(onDecide).toHaveBeenCalledWith('allow', 'always');
});

test('denying is recorded permanently so the agent stops asking', () => {
  const onDecide = renderPrompt();
  fireEvent.click(screen.getByTestId('browser-approval-deny'));
  expect(onDecide).toHaveBeenCalledWith('deny', 'always');
});

test('no button is a form default that Enter could trigger', () => {
  renderPrompt();
  for (const id of ['browser-approval-allow-thread', 'browser-approval-allow-always',
                    'browser-approval-deny']) {
    const btn = screen.getByTestId(id) as HTMLButtonElement;
    // A `submit` button inside a form is what Enter activates; these must not be.
    expect(btn.type).not.toBe('submit');
  }
});

test('nothing is decided until a button is pressed', () => {
  const onDecide = renderPrompt();
  expect(onDecide).not.toHaveBeenCalled();
});

test('a rejected decision is announced and the same choice can be retried', async () => {
  const onDecide = vi.fn().mockRejectedValueOnce(new Error('policy write failed')).mockResolvedValue(undefined);
  render(<BrowserApprovalPrompt approval={approval} onDecide={onDecide} />);
  fireEvent.click(screen.getByTestId('browser-approval-allow-thread'));
  expect(await screen.findByRole('alert')).toHaveTextContent('policy write failed');
  expect(screen.getByTestId('browser-approval-allow-thread')).toBeEnabled();
  fireEvent.click(screen.getByTestId('browser-approval-allow-thread'));
  await waitFor(() => expect(screen.queryByRole('alert')).not.toBeInTheDocument());
  expect(onDecide).toHaveBeenCalledTimes(2);
});

test('pending decisions disable every choice and announce meaningful status', async () => {
  let finish!: () => void;
  const onDecide = vi.fn(() => new Promise<void>((resolve) => { finish = resolve; }));
  render(<BrowserApprovalPrompt approval={approval} onDecide={onDecide} />);
  fireEvent.click(screen.getByTestId('browser-approval-allow-thread'));
  for (const button of screen.getAllByRole('button')) expect(button).toBeDisabled();
  expect(screen.getByRole('status')).toHaveTextContent(/saving|sending/i);
  fireEvent.click(screen.getByTestId('browser-approval-deny'));
  expect(onDecide).toHaveBeenCalledTimes(1);
  await act(async () => { finish(); });
});

test('turn-only context offers a turn grant without an invalid conversation grant', () => {
  const onDecide = renderPrompt({ allowed_lifetimes: ['turn', 'always'] });
  expect(screen.queryByTestId('browser-approval-allow-thread')).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole('button', { name: 'Allow for this turn' }));
  expect(onDecide).toHaveBeenCalledWith('allow', 'turn');
});

test.each([undefined, ['always'] as const])('without turn/thread context only persistent decisions are offered (%s)', (lifetimes) => {
  renderPrompt({ allowed_lifetimes: lifetimes ? [...lifetimes] : undefined });
  expect(screen.queryByTestId('browser-approval-allow-thread')).not.toBeInTheDocument();
  expect(screen.queryByTestId('browser-approval-allow-turn')).not.toBeInTheDocument();
  expect(screen.getByTestId('browser-approval-deny')).toBeInTheDocument();
  expect(screen.getByTestId('browser-approval-allow-always')).toBeInTheDocument();
});

test('only lifetimes explicitly available in the response can be selected', () => {
  renderPrompt({ allowed_lifetimes: ['turn'] });
  expect(screen.getByTestId('browser-approval-allow-turn')).toHaveAttribute('type', 'button');
  expect(screen.queryByTestId('browser-approval-allow-always')).not.toBeInTheDocument();
  expect(screen.queryByTestId('browser-approval-deny')).not.toBeInTheDocument();
});
