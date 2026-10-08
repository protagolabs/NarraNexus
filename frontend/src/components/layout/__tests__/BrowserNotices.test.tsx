/**
 * @file_name: BrowserNotices.test.tsx
 * @description: A browser login request must reach the user wherever they are.
 *
 * The component exists because a request placed only inside the browser panel
 * was a dead end: the agent says "complete the login" in chat, and the panel
 * may not be open. The tests pin polling, agent switching, failure recovery
 * and the feature switch.
 */
import { expect, test, vi, beforeEach, afterEach } from 'vitest';
import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter, useLocation } from 'react-router-dom';
import { useUIStore } from '@/stores/uiStore';

// `vi.mock` factories are hoisted above every other statement in the file, so
// they cannot close over ordinary consts declared here. `vi.hoisted` is the
// documented way to create the spies early enough to be referenced.
const mocks = vi.hoisted(() => ({
  getBrowserNotices: vi.fn(),
  state: { activeAgentId: 'agent_1' as string | null },
}));
const { getBrowserNotices } = mocks;

vi.mock('@/lib/api', () => ({ api: { getBrowserNotices: mocks.getBrowserNotices } }));
vi.mock('@/stores', () => ({
  useChatStore: (sel: (s: { activeAgentId: string | null }) => unknown) => sel(mocks.state),
}));

import { BrowserNotices } from '../BrowserNotices';
import { PANELS, enableOwner } from '@/platform/registries';
import { disableBuiltinUi } from '@/platform/loader';

const browserPanel = PANELS.get('browser')!;
const browserOwner = PANELS.ownerOf('browser');

const login = {
  kind: 'login', id: 'login_1', agent_id: 'agent_1', session_id: 'agent_1',
  reason: 'Complete verification for the requested account', state: 'pending',
  requested_at: '2026-09-23T00:00:00Z',
};
type Notices = { pending: (typeof login)[] };

beforeEach(() => {
  mocks.state.activeAgentId = 'agent_1';
  getBrowserNotices.mockReset();
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

function LocationProbe() {
  return <div data-testid="location">{useLocation().pathname}</div>;
}

const shell = () => <MemoryRouter><BrowserNotices /></MemoryRouter>;

test('it polls for the agent the user is actually talking to', async () => {
  getBrowserNotices.mockResolvedValue({ pending: [] });
  render(shell());
  await waitFor(() => expect(getBrowserNotices).toHaveBeenCalledWith('agent_1'));
});

test('nothing is rendered when there is no open request', async () => {
  getBrowserNotices.mockResolvedValue({ pending: [] });
  const { container } = render(shell());
  await waitFor(() => expect(getBrowserNotices).toHaveBeenCalled());
  expect(container.querySelector('[data-testid="browser-notices"]')).toBeNull();
});

test('no agent selected means nothing is polled', async () => {
  mocks.state.activeAgentId = null;
  render(shell());
  await new Promise((r) => setTimeout(r, 10));
  expect(getBrowserNotices).not.toHaveBeenCalled();
});

test('a persisted login request opens the browser without taking control', async () => {
  getBrowserNotices.mockResolvedValue({ pending: [login] });
  render(<MemoryRouter initialEntries={['/app/settings']}><BrowserNotices /><LocationProbe /></MemoryRouter>);
  expect(await screen.findByText(login.reason)).toBeInTheDocument();
  fireEvent.click(screen.getByRole('button', { name: /open browser/i }));
  expect(screen.getByTestId('location')).toHaveTextContent('/app/chat');
  expect(useUIStore.getState().pendingPanel).toBe('browser');
  expect(useUIStore.getState().pendingPanelMode).toBe('open');
  expect(screen.getByText(login.reason)).toBeInTheDocument();
});

test('login requests remain visible during takeover and disappear only after the server removes them', async () => {
  vi.useFakeTimers();
  getBrowserNotices.mockResolvedValueOnce({ pending: [login] })
    .mockResolvedValueOnce({ pending: [{ ...login, state: 'in_control' }] })
    .mockResolvedValue({ pending: [] });
  render(shell());
  await act(async () => {});
  expect(screen.getByText(login.reason)).toBeInTheDocument();
  await act(async () => { await vi.advanceTimersByTimeAsync(2000); });
  expect(screen.getByTestId('browser-login-notice')).toHaveTextContent(/return control/i);
  expect(screen.queryByText(/login succeeded|signed in successfully/i)).not.toBeInTheDocument();
  await act(async () => { await vi.advanceTimersByTimeAsync(2000); });
  expect(screen.queryByTestId('browser-login-notice')).not.toBeInTheDocument();
});

test('another agent\'s request in the response is never shown', async () => {
  getBrowserNotices.mockResolvedValue({ pending: [{ ...login, agent_id: 'agent_other', reason: 'not yours' }] });
  render(shell());
  await waitFor(() => expect(getBrowserNotices).toHaveBeenCalled());
  expect(screen.queryByText('not yours')).not.toBeInTheDocument();
});

test.each(['resolve', 'reject'] as const)('a late %s from a previous agent cannot replace the current notice', async (outcome) => {
  const old = deferred<Notices>();
  getBrowserNotices.mockReturnValueOnce(old.promise).mockResolvedValue({ pending: [
    { ...login, id: 'login_2', agent_id: 'agent_2', reason: 'Current agent login' },
  ] });
  const view = render(shell());
  mocks.state.activeAgentId = 'agent_2';
  view.rerender(shell());
  await screen.findByText('Current agent login');
  await act(async () => {
    if (outcome === 'resolve') old.resolve({ pending: [login] });
    else old.reject(new Error('old backend failure'));
  });
  expect(screen.getByText('Current agent login')).toBeInTheDocument();
  expect(screen.queryByText(login.reason)).not.toBeInTheDocument();
  expect(screen.queryByRole('alert')).not.toBeInTheDocument();
});

test('switching agents immediately hides the old notice while the next poll is pending', async () => {
  getBrowserNotices.mockResolvedValueOnce({ pending: [login] }).mockReturnValue(new Promise(() => {}));
  const view = render(shell());
  await screen.findByText(login.reason);
  mocks.state.activeAgentId = 'agent_2';
  view.rerender(shell());
  expect(screen.queryByText(login.reason)).not.toBeInTheDocument();
  mocks.state.activeAgentId = null;
  view.rerender(shell());
  expect(screen.queryByTestId('browser-notices')).not.toBeInTheDocument();
});

test('slow polls do not overlap or continue after unmount', async () => {
  vi.useFakeTimers();
  const request = deferred<Notices>();
  getBrowserNotices.mockReturnValue(request.promise);
  const view = render(shell());
  await act(async () => { await vi.advanceTimersByTimeAsync(8000); });
  expect(getBrowserNotices).toHaveBeenCalledTimes(1);
  view.unmount();
  await act(async () => { request.resolve({ pending: [login] }); });
  await act(async () => { await vi.advanceTimersByTimeAsync(8000); });
  expect(getBrowserNotices).toHaveBeenCalledTimes(1);
});

test('a failing poll does not leave a stale notice on screen', async () => {
  getBrowserNotices.mockResolvedValueOnce({ pending: [login] }).mockRejectedValue(new Error('backend restarting'));
  vi.useFakeTimers();
  render(shell());
  await act(async () => {});
  expect(screen.getByText(login.reason)).toBeInTheDocument();
  await act(async () => { await vi.advanceTimersByTimeAsync(2000); });
  expect(screen.queryByText(login.reason)).not.toBeInTheDocument();
});

test('poll failures are announced and a manual retry restores notices', async () => {
  getBrowserNotices.mockRejectedValueOnce(new Error('backend restarting'))
    .mockResolvedValue({ pending: [login] });
  render(shell());
  expect(await screen.findByRole('alert')).toHaveTextContent('backend restarting');
  expect(screen.getByRole('alert')).toHaveTextContent(/login requests/i);
  fireEvent.click(screen.getByRole('button', { name: /try again|retry/i }));
  await screen.findByText(login.reason);
  expect(screen.queryByRole('alert')).not.toBeInTheDocument();
});

test('a disabled browser feature never starts polling', () => {
  disableBuiltinUi('builtin.browser');
  render(shell());
  expect(getBrowserNotices).not.toHaveBeenCalled();
  expect(screen.queryByTestId('browser-notices')).not.toBeInTheDocument();
});

test('disabling the browser feature removes notices and stops polling', async () => {
  vi.useFakeTimers();
  getBrowserNotices.mockResolvedValue({ pending: [login] });
  render(shell());
  await act(async () => {});
  expect(screen.getByText(login.reason)).toBeInTheDocument();
  act(() => { disableBuiltinUi('builtin.browser'); });
  expect(screen.queryByText(login.reason)).not.toBeInTheDocument();
  await act(async () => { await vi.advanceTimersByTimeAsync(6000); });
  expect(getBrowserNotices).toHaveBeenCalledTimes(1);
});
