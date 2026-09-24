/**
 * @file_name: BrowserApprovalNotice.test.tsx
 * @description: The approval prompt must reach the user wherever they are.
 *
 * This component exists because the prompt was twice placed somewhere the
 * user was not: first only inside the browser panel (which exists only when a
 * URL tab is open in stream mode), while the refusal is read in chat. The
 * tests below pin the parts that made it a dead end.
 */
import { expect, test, vi, beforeEach, afterEach } from 'vitest';
import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter, useLocation } from 'react-router-dom';
import { useUIStore } from '@/stores/uiStore';

// `vi.mock` factories are hoisted above every other statement in the file, so
// they cannot close over ordinary consts declared here. `vi.hoisted` is the
// documented way to create the spies early enough to be referenced.
const mocks = vi.hoisted(() => ({
  getBrowserApprovals: vi.fn(),
  resolveBrowserApproval: vi.fn(async () => ({ ok: true })),
  state: { activeAgentId: 'agent_1' as string | null },
}));
const { getBrowserApprovals, resolveBrowserApproval } = mocks;

vi.mock('@/lib/api', () => ({
  api: {
    getBrowserApprovals: mocks.getBrowserApprovals,
    resolveBrowserApproval: mocks.resolveBrowserApproval,
  },
}));
vi.mock('@/stores', () => ({
  useChatStore: (sel: (s: { activeAgentId: string | null }) => unknown) => sel(mocks.state),
}));

import { BrowserApprovalNotice } from '../BrowserApprovalNotice';
import { PANELS, enableOwner } from '@/platform/registries';
import { disableBuiltinUi } from '@/platform/loader';

const browserPanel = PANELS.get('browser')!;
const browserOwner = PANELS.ownerOf('browser');

const pending = {
  id: 'appr_1', agent_id: 'agent_1', origin: 'https://www.baidu.com',
  capability: 'downloads', requested_at: 0,
  allowed_lifetimes: ['thread', 'always'],
};

beforeEach(() => {
  mocks.state.activeAgentId = 'agent_1';
  getBrowserApprovals.mockReset();
  resolveBrowserApproval.mockReset().mockResolvedValue({ ok: true });
});

afterEach(() => {
  cleanup();
  vi.useRealTimers();
  enableOwner('builtin.browser');
  PANELS.register('browser', browserPanel, { owner: browserOwner, replace: true });
});

function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (reason: Error) => void;
  const promise = new Promise<T>((res, rej) => { resolve = res; reject = rej; });
  return { promise, resolve, reject };
}

test('a pending approval is shown without any panel being open', async () => {
  getBrowserApprovals.mockResolvedValue({ pending: [pending] });
  render(<BrowserApprovalNotice />);
  await waitFor(() => expect(screen.getByTestId('browser-approval')).toBeTruthy());
  expect(screen.getByTestId('browser-approval-origin').textContent).toContain('baidu.com');
});

test('nothing is rendered when there is nothing to ask', async () => {
  getBrowserApprovals.mockResolvedValue({ pending: [] });
  const { container } = render(<BrowserApprovalNotice />);
  await waitFor(() => expect(getBrowserApprovals).toHaveBeenCalled());
  expect(container.querySelector('[data-testid="browser-approval-notice"]')).toBeNull();
});

test('it asks about the agent the user is actually talking to', async () => {
  getBrowserApprovals.mockResolvedValue({ pending: [] });
  render(<BrowserApprovalNotice />);
  await waitFor(() => expect(getBrowserApprovals).toHaveBeenCalledWith('agent_1'));
});

test('answering sends the decision and its lifetime', async () => {
  getBrowserApprovals.mockResolvedValue({ pending: [pending] });
  render(<BrowserApprovalNotice />);
  await waitFor(() => screen.getByTestId('browser-approval-allow-thread'));

  fireEvent.click(screen.getByTestId('browser-approval-allow-thread'));

  await waitFor(() =>
    expect(resolveBrowserApproval).toHaveBeenCalledWith('appr_1', 'allow', 'thread'));
});

test('an answered question disappears immediately', async () => {
  // Leaving it on screen until the next poll invites a second, contradictory
  // answer to a question that is already settled.
  getBrowserApprovals.mockResolvedValue({ pending: [pending] });
  render(<BrowserApprovalNotice />);
  await waitFor(() => screen.getByTestId('browser-approval-allow-thread'));

  fireEvent.click(screen.getByTestId('browser-approval-allow-thread'));

  await waitFor(() => expect(screen.queryByTestId('browser-approval')).toBeNull());
});

test('a failing poll does not leave a stale prompt on screen', async () => {
  getBrowserApprovals.mockRejectedValue(new Error('backend restarting'));
  const { container } = render(<BrowserApprovalNotice />);
  await waitFor(() => expect(getBrowserApprovals).toHaveBeenCalled());
  expect(container.querySelector('[data-testid="browser-approval"]')).toBeNull();
});

test('no agent selected means nothing is asked', async () => {
  mocks.state.activeAgentId = null;
  render(<BrowserApprovalNotice />);
  await new Promise((r) => setTimeout(r, 10));
  expect(getBrowserApprovals).not.toHaveBeenCalled();
});

const login = {
  kind: 'login', id: 'login_1', agent_id: 'agent_1', session_id: 'agent_1',
  reason: 'Complete verification for the requested account', state: 'pending',
  requested_at: '2026-09-23T00:00:00Z',
};

function LocationProbe() {
  return <div data-testid="location">{useLocation().pathname}</div>;
}

test('a persisted login request opens the browser without resolving a permission or taking control', async () => {
  getBrowserApprovals.mockResolvedValue({ pending: [login] });
  render(<MemoryRouter initialEntries={['/app/settings']}><BrowserApprovalNotice /><LocationProbe /></MemoryRouter>);
  expect(await screen.findByText(login.reason)).toBeInTheDocument();
  expect(screen.queryByTestId('browser-approval')).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole('button', { name: /open browser/i }));
  expect(screen.getByTestId('location')).toHaveTextContent('/app/chat');
  expect(useUIStore.getState().pendingPanel).toBe('browser');
  expect(useUIStore.getState().pendingPanelMode).toBe('open');
  expect(resolveBrowserApproval).not.toHaveBeenCalled();
  expect(screen.getByText(login.reason)).toBeInTheDocument();
});

test('login requests remain visible during takeover and disappear only after the server removes them', async () => {
  vi.useFakeTimers();
  getBrowserApprovals.mockResolvedValueOnce({ pending: [login] })
    .mockResolvedValueOnce({ pending: [{ ...login, state: 'in_control' }] })
    .mockResolvedValue({ pending: [] });
  render(<MemoryRouter><BrowserApprovalNotice /></MemoryRouter>);
  await act(async () => {});
  expect(screen.getByText(login.reason)).toBeInTheDocument();
  await act(async () => { await vi.advanceTimersByTimeAsync(2000); });
  expect(screen.getByTestId('browser-login-notice')).toHaveTextContent(/return control/i);
  expect(screen.queryByText(/login succeeded|signed in successfully/i)).not.toBeInTheDocument();
  await act(async () => { await vi.advanceTimersByTimeAsync(2000); });
  expect(screen.queryByTestId('browser-login-notice')).not.toBeInTheDocument();
});

test.each(['resolve', 'reject'] as const)('a late %s from a previous agent cannot replace the current prompt', async (outcome) => {
  const old = deferred<{ pending: typeof pending[] }>();
  getBrowserApprovals.mockReturnValueOnce(old.promise).mockResolvedValue({ pending: [
    { ...pending, id: 'appr_2', agent_id: 'agent_2', origin: 'https://current.example' },
  ] });
  const view = render(<BrowserApprovalNotice />);
  mocks.state.activeAgentId = 'agent_2';
  view.rerender(<BrowserApprovalNotice />);
  await screen.findByText('https://current.example');
  await act(async () => {
    if (outcome === 'resolve') old.resolve({ pending: [pending] });
    else old.reject(new Error('old backend failure'));
  });
  expect(screen.getByText('https://current.example')).toBeInTheDocument();
  expect(screen.queryByText(pending.origin)).not.toBeInTheDocument();
  expect(screen.queryByRole('alert')).not.toBeInTheDocument();
});

test('switching agents immediately hides the old prompt while the next poll is pending', async () => {
  getBrowserApprovals.mockResolvedValueOnce({ pending: [pending] })
    .mockReturnValue(new Promise(() => {}));
  const view = render(<BrowserApprovalNotice />);
  await screen.findByTestId('browser-approval');
  mocks.state.activeAgentId = 'agent_2';
  view.rerender(<BrowserApprovalNotice />);
  expect(screen.queryByTestId('browser-approval')).not.toBeInTheDocument();
  mocks.state.activeAgentId = null;
  view.rerender(<BrowserApprovalNotice />);
  expect(screen.queryByTestId('browser-approval-notice')).not.toBeInTheDocument();
});

test('slow approval polls do not overlap or continue after unmount', async () => {
  vi.useFakeTimers();
  const request = deferred<{ pending: typeof pending[] }>();
  getBrowserApprovals.mockReturnValue(request.promise);
  const view = render(<BrowserApprovalNotice />);
  await act(async () => { await vi.advanceTimersByTimeAsync(8000); });
  expect(getBrowserApprovals).toHaveBeenCalledTimes(1);
  view.unmount();
  await act(async () => { request.resolve({ pending: [pending] }); });
  await act(async () => { await vi.advanceTimersByTimeAsync(8000); });
  expect(getBrowserApprovals).toHaveBeenCalledTimes(1);
});

test('an in-flight poll cannot restore a successfully answered approval', async () => {
  vi.useFakeTimers();
  const stale = deferred<{ pending: typeof pending[] }>();
  getBrowserApprovals.mockResolvedValueOnce({ pending: [pending] }).mockReturnValueOnce(stale.promise);
  render(<BrowserApprovalNotice />);
  await act(async () => {});
  await act(async () => { await vi.advanceTimersByTimeAsync(2000); });
  fireEvent.click(screen.getByTestId('browser-approval-allow-thread'));
  await act(async () => {});
  expect(screen.queryByTestId('browser-approval')).not.toBeInTheDocument();
  await act(async () => { stale.resolve({ pending: [pending] }); });
  expect(screen.queryByTestId('browser-approval')).not.toBeInTheDocument();
});

test('a failed decision response keeps the question and exposes a retryable error', async () => {
  getBrowserApprovals.mockResolvedValue({ pending: [pending] });
  resolveBrowserApproval.mockResolvedValueOnce({ ok: false });
  render(<BrowserApprovalNotice />);
  fireEvent.click(await screen.findByTestId('browser-approval-allow-thread'));
  expect(await screen.findByRole('alert')).toHaveTextContent(/could not|failed/i);
  expect(screen.getByTestId('browser-approval-allow-thread')).toBeEnabled();
  fireEvent.click(screen.getByTestId('browser-approval-allow-thread'));
  await waitFor(() => expect(screen.queryByTestId('browser-approval')).not.toBeInTheDocument());
});

test('poll failures are announced and a manual retry restores approvals', async () => {
  getBrowserApprovals.mockRejectedValueOnce(new Error('backend restarting'))
    .mockResolvedValue({ pending: [pending] });
  render(<BrowserApprovalNotice />);
  expect(await screen.findByRole('alert')).toHaveTextContent('backend restarting');
  fireEvent.click(screen.getByRole('button', { name: /try again|retry/i }));
  await screen.findByTestId('browser-approval');
  expect(screen.queryByRole('alert')).not.toBeInTheDocument();
});

test('a disabled browser feature never starts approval requests', () => {
  disableBuiltinUi('builtin.browser');
  render(<BrowserApprovalNotice />);
  expect(getBrowserApprovals).not.toHaveBeenCalled();
  expect(screen.queryByTestId('browser-approval-notice')).not.toBeInTheDocument();
});

test('disabling the browser feature removes prompts and stops polling', async () => {
  vi.useFakeTimers();
  getBrowserApprovals.mockResolvedValue({ pending: [pending] });
  render(<BrowserApprovalNotice />);
  await act(async () => {});
  expect(screen.getByTestId('browser-approval')).toBeInTheDocument();
  act(() => { disableBuiltinUi('builtin.browser'); });
  expect(screen.queryByTestId('browser-approval')).not.toBeInTheDocument();
  await act(async () => { await vi.advanceTimersByTimeAsync(6000); });
  expect(getBrowserApprovals).toHaveBeenCalledTimes(1);
});
