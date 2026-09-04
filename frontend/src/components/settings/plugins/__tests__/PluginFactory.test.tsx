/**
 * @file_name: PluginFactory.test.tsx
 * @author: Bin Liang
 * @date: 2026-09-03
 * @description: The factory page lists plugins with state, installs with a permissions disclosure, drives enable/disable/rollback, shows safe mode + bisect, and hides in cloud.
 */
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';

import { PluginFactory } from '../PluginFactory';
import type { FactoryListResponse, FactoryPlugin } from '@/types';

const mocks = vi.hoisted(() => ({
  factoryList: vi.fn(),
  factoryInstall: vi.fn(),
  factoryAction: vi.fn(),
  factoryRollback: vi.fn(),
  factoryLeaveSafeMode: vi.fn(),
  factoryBisect: vi.fn(),
  factoryBisectAnswer: vi.fn(),
  factoryErrors: vi.fn(),
}));
vi.mock('@/lib/api', () => ({ api: mocks }));

function plugin(over: Partial<FactoryPlugin> = {}): FactoryPlugin {
  return {
    id: 'acme.weather', display_name: 'Weather', description: 'Forecasts', version: '1.2.0', mode: 'copy', path: '/p',
    source: { type: 'github', repo: 'acme/w', tag: '1.2.0' }, enabled: true, state: 'enabled', scope: 'global', last_error: null,
    crash_count: 0, warnings: ['tag differs'], permissions: { network: ['api.weather.com'] }, permissions_acknowledged: true,
    installed_by: 'user', installed_at: '', provides: ['backend.routes'], size: {}, loaded: true, isolated: null, recent_errors: 2,
    frontend: null, activation_events: ['onStartup'], protected: false, ...over,
  };
}

function listing(over: Partial<NonNullable<FactoryListResponse['data']>> = {}): FactoryListResponse {
  return { success: true, data: { plugins: [plugin()], safe_mode: false, safe_mode_reason: '', bisect: null, cloud_managed: false, boot: null, ...over } };
}

beforeEach(() => {
  mocks.factoryList.mockResolvedValue(listing());
  mocks.factoryInstall.mockResolvedValue({ success: true, data: { id: 'acme.new', version: '1.0.0', path: '/x', mode: 'link', warnings: [], deps_installed: [], permissions: { subprocess: true }, restart_required: true } });
  mocks.factoryAction.mockResolvedValue({ success: true });
  mocks.factoryRollback.mockResolvedValue({ success: true });
  mocks.factoryLeaveSafeMode.mockResolvedValue({ success: true });
  mocks.factoryBisect.mockResolvedValue({ success: true, data: {} });
  mocks.factoryBisectAnswer.mockResolvedValue({ success: true, data: {} });
  mocks.factoryErrors.mockResolvedValue({ success: true, data: { errors: [{ at: 1, kind: 'render', message: 'boom', stack: '' }] } });
});
afterEach(() => vi.clearAllMocks());

it('lists plugins with state, warnings and provides, and drives disable/uninstall/rollback', async () => {
  render(<PluginFactory />);
  expect(await screen.findByText('Weather')).toBeInTheDocument();
  expect(screen.getByText('enabled')).toBeInTheDocument();
  expect(screen.getByText('tag differs')).toBeInTheDocument();
  expect(screen.getByText('backend.routes')).toBeInTheDocument();
  fireEvent.click(screen.getByText('Disable'));
  await waitFor(() => expect(mocks.factoryAction).toHaveBeenCalledWith('acme.weather', 'disable'));
  expect(await screen.findByRole('status')).toHaveTextContent(/Restart NarraNexus/);
  fireEvent.click(screen.getByText('Uninstall'));
  await waitFor(() => expect(mocks.factoryAction).toHaveBeenCalledWith('acme.weather', 'uninstall'));
  fireEvent.click(screen.getByText('Roll back to last known good'));
  await waitFor(() => expect(mocks.factoryRollback).toHaveBeenCalled());
  fireEvent.click(screen.getByText('Errors (2)'));
  expect(await screen.findByText('[render] boom')).toBeInTheDocument();
});

it('installs from a source and shows the permissions disclosure until acknowledged', async () => {
  render(<PluginFactory />);
  await screen.findByText('Weather');
  const input = screen.getByLabelText(/owner\/repo@1.2.0/);
  fireEvent.change(input, { target: { value: 'acme/new@1.0.0' } });
  fireEvent.click(screen.getByText('Install'));
  await waitFor(() => expect(mocks.factoryInstall).toHaveBeenCalledWith('acme/new@1.0.0'));
  const dialog = await screen.findByTestId('permissions-dialog');
  expect(dialog).toHaveTextContent('Runs subprocesses');
  fireEvent.click(screen.getByText('I understand, enable it'));
  await waitFor(() => expect(mocks.factoryAction).toHaveBeenCalledWith('acme.new', 'acknowledge-permissions'));
  await waitFor(() => expect(screen.queryByTestId('permissions-dialog')).toBeNull());
});

it('shows the safe-mode banner and walks the bisect wizard', async () => {
  mocks.factoryList.mockResolvedValue(listing({ safe_mode: true, safe_mode_reason: '2 boots failed', plugins: [plugin(), plugin({ id: 'acme.b', display_name: 'B' })] }));
  render(<PluginFactory />);
  expect(await screen.findByTestId('safe-mode-banner')).toHaveTextContent('2 boots failed');
  fireEvent.click(screen.getByText('Find the plugin that breaks startup'));
  await waitFor(() => expect(mocks.factoryBisect).toHaveBeenCalledWith('start'));
  mocks.factoryList.mockResolvedValue(listing({ safe_mode: true, safe_mode_reason: 'x', bisect: { candidates: ['acme.weather', 'acme.b'], trial: ['acme.weather'], cleared: [] } }));
  fireEvent.click(screen.getByText('Leave safe mode'));
  await waitFor(() => expect(mocks.factoryLeaveSafeMode).toHaveBeenCalled());
  expect(await screen.findByTestId('bisect-wizard')).toHaveTextContent('acme.weather');
  fireEvent.click(screen.getByText('It started fine'));
  await waitFor(() => expect(mocks.factoryBisectAnswer).toHaveBeenCalledWith(true));
});

it('renders nothing in cloud mode and an error when the list fails', async () => {
  mocks.factoryList.mockResolvedValue(listing({ cloud_managed: true }));
  const { container, unmount } = render(<PluginFactory />);
  await waitFor(() => expect(mocks.factoryList).toHaveBeenCalled());
  await waitFor(() => expect(container.querySelector('[data-testid="plugin-factory"]')).toBeNull());
  unmount();
  mocks.factoryList.mockRejectedValue(new Error('offline'));
  render(<PluginFactory />);
  expect(await screen.findByText('offline')).toBeInTheDocument();
});

it('protected plugins cannot be disabled or uninstalled', async () => {
  mocks.factoryList.mockResolvedValue(listing({ plugins: [plugin({ protected: true, source: { type: 'local' } })] }));
  render(<PluginFactory />);
  await screen.findByText('Weather');
  expect((screen.getByText('Disable').closest('button') as HTMLButtonElement).disabled).toBe(true);
  expect(screen.queryByText('Uninstall')).toBeNull();
  expect(screen.queryByText('Upgrade')).toBeNull();
});
