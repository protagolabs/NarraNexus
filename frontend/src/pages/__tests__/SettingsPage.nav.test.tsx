/**
 * Nav test for SettingsPage (Chat UI v4): account/billing/subscription moved
 * to the user-scoped /app/account page and bundle entries to the sidebar —
 * neither appears in the Settings nav anymore. The legacy ?tab=account deep
 * link (Stripe's post-payment return target) must REDIRECT to /app/account
 * with the query preserved instead of falling back to a random pane. Heavy
 * content panels are stubbed so the test only exercises nav + redirect.
 */
import { fireEvent, render, screen } from '@testing-library/react';
import { beforeEach, describe, expect, test, vi } from 'vitest';

const { mockT } = vi.hoisted(() => {
  const copy: Record<string, string> = {
    'pages.settings.nav.providers': 'LLM Providers',
    'pages.settings.nav.artifacts': 'Artifacts',
    'pages.settings.nav.modelDefaults': 'Model Defaults',
  };
  return { mockT: (key: string) => copy[key] ?? key };
});

vi.mock('react-i18next', () => ({ useTranslation: () => ({ t: mockT }) }));
let mockSearch = '';
vi.mock('react-router-dom', () => ({
  useNavigate: () => vi.fn(),
  useSearchParams: () => [new URLSearchParams(mockSearch), vi.fn()] as const,
  Navigate: ({ to }: { to: string }) => <div data-testid="redirect" data-to={to} />,
}));
vi.mock('@/components/settings/ProviderSettings', () => ({
  ProviderSettings: () => <div data-testid="providers-pane" />,
}));
vi.mock('@/components/settings/ModelDefaultsSettings', () => ({ ModelDefaultsSettings: () => <div /> }));
vi.mock('@/components/settings/PrivacySettings', () => ({
  PrivacySettings: () => <div data-testid="privacy-pane" />,
}));
vi.mock('@/components/settings/ArtifactsSection', () => ({
  default: () => <div data-testid="artifacts-pane" />,
}));
vi.mock('@/lib/tauri', () => ({ isTauri: () => false, kickUpdaterCheck: vi.fn(), restartForUpdate: vi.fn() }));
vi.mock('@/stores/updaterStore', () => ({ useUpdaterStore: (sel: (s: unknown) => unknown) => sel({ status: 'idle' }) }));

import SettingsPage from '../SettingsPage';

describe('SettingsPage nav — app-scoped items only (v4)', () => {
  beforeEach(() => {
    mockSearch = '';
  });

  test('account and bundle entries are gone from the nav', () => {
    render(<SettingsPage />);
    expect(screen.queryByRole('button', { name: /account/i })).toBeNull();
    expect(screen.queryByRole('button', { name: /bundle/i })).toBeNull();
    expect(screen.getByRole('button', { name: /LLM Providers/ })).toBeTruthy();
  });
});

// ── ?tab= deep link ─────────────────────────────────────────────────────────
// Stripe drops the payer on /app/settings?tab=account&status=… (backend
// billing.py::_return_urls). Account lives on /app/account now, so that URL
// must forward there with the whole query preserved — landing the payer on a
// random Settings pane would read as "my payment went nowhere".
describe('SettingsPage ?tab= deep link', () => {
  test('tab=account redirects to /app/account preserving the query', () => {
    mockSearch = 'tab=account&status=success';
    render(<SettingsPage />);
    const redirect = screen.getByTestId('redirect');
    expect(redirect.getAttribute('data-to')).toContain('/app/account?');
    expect(redirect.getAttribute('data-to')).toContain('status=success');
  });

  test('opens the pane named in the URL, not the default first item', () => {
    mockSearch = 'tab=artifacts';
    render(<SettingsPage />);
    expect(screen.getByTestId('artifacts-pane')).toBeTruthy();
    expect(screen.queryByTestId('providers-pane')).toBeNull();
  });

  test('unknown tab falls back to the first visible item', () => {
    mockSearch = 'tab=not-a-pane';
    render(<SettingsPage />);
    expect(screen.getByTestId('providers-pane')).toBeTruthy();
  });

  test('tab=privacy opens the privacy pane — the telemetry notice deep-links here', () => {
    // TelemetryNotice navigates to /app/settings?tab=privacy; if this
    // ever falls back to the first pane, "turn it off in settings"
    // becomes a dead promise.
    mockSearch = 'tab=privacy';
    render(<SettingsPage />);
    expect(screen.getByTestId('privacy-pane')).toBeTruthy();
  });

  test('the URL seeds the pane but does not lock it — nav clicks still work', () => {
    mockSearch = 'tab=artifacts';
    render(<SettingsPage />);
    fireEvent.click(screen.getByRole('button', { name: /LLM Providers/ }));
    expect(screen.getByTestId('providers-pane')).toBeTruthy();
    expect(screen.queryByTestId('artifacts-pane')).toBeNull();
  });
});
