/**
 * PrivacySettings — the two consent toggles' load/flip/guard behavior.
 * The telemetry half's contract: `controllable=false` renders a DISABLED
 * switch with the managed note (never hides it — an invisible switch
 * reads as "there is no telemetry", which would be false).
 */
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, test, vi } from 'vitest';

const { mockApi } = vi.hoisted(() => ({
  mockApi: {
    getAnalyticsOptOut: vi.fn(),
    setAnalyticsOptOut: vi.fn(),
    getTelemetryConsent: vi.fn(),
    setTelemetryOptOut: vi.fn(),
  },
}));
vi.mock('@/lib/api', () => ({ api: mockApi }));
vi.mock('react-i18next', () => ({
  useTranslation: () => ({ t: (key: string) => key }),
}));

import { PrivacySettings } from '../PrivacySettings';

describe('PrivacySettings', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mockApi.getAnalyticsOptOut.mockResolvedValue(false);
    mockApi.setAnalyticsOptOut.mockResolvedValue(undefined);
    mockApi.setTelemetryOptOut.mockResolvedValue(undefined);
  });

  test('telemetry on + controllable: toggle flips the opt-out marker', async () => {
    mockApi.getTelemetryConsent.mockResolvedValue({
      mode: 'full', source: 'default', opted_out: false, controllable: true,
    });
    render(<PrivacySettings />);
    const toggle = await screen.findByRole('switch', {
      name: 'pages.settings.privacy.telemetryTitle',
    });
    await waitFor(() => expect(toggle.getAttribute('aria-checked')).toBe('true'));
    fireEvent.click(toggle);
    await waitFor(() =>
      expect(mockApi.setTelemetryOptOut).toHaveBeenCalledWith(true),
    );
    expect(toggle.getAttribute('aria-checked')).toBe('false');
  });

  test('failed write reverts the optimistic flip', async () => {
    mockApi.getTelemetryConsent.mockResolvedValue({
      mode: 'full', source: 'default', opted_out: false, controllable: true,
    });
    mockApi.setTelemetryOptOut.mockRejectedValue(new Error('boom'));
    render(<PrivacySettings />);
    const toggle = await screen.findByRole('switch', {
      name: 'pages.settings.privacy.telemetryTitle',
    });
    await waitFor(() => expect(toggle.getAttribute('aria-checked')).toBe('true'));
    fireEvent.click(toggle);
    await waitFor(() =>
      expect(toggle.getAttribute('aria-checked')).toBe('true'),
    );
  });

  test('env-managed install: switch disabled, managed note shown, no writes', async () => {
    mockApi.getTelemetryConsent.mockResolvedValue({
      mode: 'meta', source: 'env', opted_out: false, controllable: false,
    });
    render(<PrivacySettings />);
    const toggle = await screen.findByRole('switch', {
      name: 'pages.settings.privacy.telemetryTitle',
    });
    await waitFor(() =>
      expect(
        screen.getByText('pages.settings.privacy.telemetryManaged'),
      ).toBeTruthy(),
    );
    expect((toggle as HTMLButtonElement).disabled).toBe(true);
    fireEvent.click(toggle);
    expect(mockApi.setTelemetryOptOut).not.toHaveBeenCalled();
  });

  test('analytics toggle round-trips through the per-user endpoint', async () => {
    mockApi.getTelemetryConsent.mockResolvedValue({
      mode: 'full', source: 'default', opted_out: false, controllable: true,
    });
    mockApi.getAnalyticsOptOut.mockResolvedValue(true); // opted out
    render(<PrivacySettings />);
    const toggle = await screen.findByRole('switch', {
      name: 'pages.settings.privacy.analyticsTitle',
    });
    await waitFor(() => expect(toggle.getAttribute('aria-checked')).toBe('false'));
    fireEvent.click(toggle);
    await waitFor(() =>
      expect(mockApi.setAnalyticsOptOut).toHaveBeenCalledWith(false),
    );
  });
});
