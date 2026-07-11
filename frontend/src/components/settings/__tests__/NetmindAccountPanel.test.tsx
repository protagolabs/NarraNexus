/**
 * @file NetmindAccountPanel.test.tsx
 * @description Covers the plan × runway state machine of the merged Account &
 * Subscription card: S0 local hidden; plan states (free / pro_active /
 * pro_cancelled) drive the badge + top status + management action; runway
 * health (healthy / low) decides whether spend controls stay behind "Manage"
 * or ONE contextual action is promoted (free→upsell Pro, pro→top-up). Also
 * covers the runway view (free-tier bar / grant / balance / prefer toggle),
 * the read-only module-F status, recharge flows, and the activity ledger.
 * api + i18n + runtimeStore are mocked — no network.
 */
import { render, screen, waitFor, fireEvent } from '@testing-library/react';
import { afterEach, beforeEach, expect, test, vi } from 'vitest';
import { NetmindAccountPanel } from '../NetmindAccountPanel';

// i18n: return the inline default string (2nd arg) with {{var}} interpolation
// applied, so assertions can read real, fully-resolved copy ("62% left").
vi.mock('react-i18next', () => ({
  useTranslation: () => ({
    t: (_k: string, d?: unknown, o?: Record<string, unknown>) => {
      const s = typeof d === 'string' ? d : _k;
      const opts = (d && typeof d === 'object' ? (d as Record<string, unknown>) : o) ?? {};
      return s.replace(/\{\{(\w+)\}\}/g, (m, v) => (v in opts ? String(opts[v]) : m));
    },
  }),
}));

let mockMode = 'cloud-web';
vi.mock('@/stores/runtimeStore', () => ({
  useRuntimeStore: (sel: (s: { mode: string }) => unknown) => sel({ mode: mockMode }),
}));

const mockGetSubscription = vi.fn();
const mockGetFeeInfo = vi.fn();
const mockGetRecords = vi.fn();
const mockGetMyQuota = vi.fn();
const mockGetPlans = vi.fn();
const mockSetQuotaPreference = vi.fn();
const mockSubscribe = vi.fn();
const mockCancel = vi.fn();
const mockReactivate = vi.fn();
const mockRecharge = vi.fn();
const mockRechargeStatus = vi.fn();
const mockGetProviders = vi.fn();
vi.mock('@/lib/api', () => ({
  api: {
    getSubscription: (...a: unknown[]) => mockGetSubscription(...a),
    getFeeInfo: (...a: unknown[]) => mockGetFeeInfo(...a),
    getRecords: (...a: unknown[]) => mockGetRecords(...a),
    getMyQuota: (...a: unknown[]) => mockGetMyQuota(...a),
    getPlans: (...a: unknown[]) => mockGetPlans(...a),
    setQuotaPreference: (...a: unknown[]) => mockSetQuotaPreference(...a),
    subscribe: (...a: unknown[]) => mockSubscribe(...a),
    cancelSubscription: (...a: unknown[]) => mockCancel(...a),
    reactivateSubscription: (...a: unknown[]) => mockReactivate(...a),
    recharge: (...a: unknown[]) => mockRecharge(...a),
    rechargeStatus: (...a: unknown[]) => mockRechargeStatus(...a),
    getProviders: (...a: unknown[]) => mockGetProviders(...a),
  },
}));

const mockOpenExternal = vi.fn().mockResolvedValue(undefined);
vi.mock('@/lib/platform', () => ({
  platform: { openExternal: (...a: unknown[]) => mockOpenExternal(...a) },
}));

// ── Fixtures ────────────────────────────────────────────────────────────────
// Shapes mirror the verified backend contracts: quota (backend/routes/quota.py,
// NO envelope), plans (billing.py verbatim proxy, {success,data:{plans}}
// envelope), fee-info (all-optional NetMind passthrough).

const NETMIND_CONNECTED = {
  success: true,
  data: { providers: { p1: { source: 'netmind' } }, slots: {} },
};

const PRO_PLAN = {
  plan_id: 'pro',
  name: 'NetMind Pro',
  quota_limits: { rpm: 600 },
  features: { support: true, member_price: true },
  monthly_grant_usd: 19,
  prices: [{ period: 'month', currency: 'USD', stripe_price_id: 'price_x' }],
};

// input 62% left / output ~79% left → bar shows the more depleted: 62%.
const QUOTA_ACTIVE = {
  enabled: true,
  status: 'active',
  remaining_input_tokens: 124_000,
  remaining_output_tokens: 119_000,
  initial_input_tokens: 200_000,
  initial_output_tokens: 150_000,
  granted_input_tokens: 0,
  granted_output_tokens: 0,
  used_input_tokens: 76_000,
  used_output_tokens: 31_000,
  prefer_system_override: true,
};
const QUOTA_EXHAUSTED = {
  ...QUOTA_ACTIVE,
  status: 'exhausted',
  remaining_input_tokens: 0,
  remaining_output_tokens: 0,
};

const FEE_RICH = {
  success: true,
  data: {
    eligible: true,
    checks: { has_arrears: false },
    metrics: { free_credit: '12.50', monthly_free_credit: '19.00' },
  },
};
const FEE_POOR = {
  success: true,
  data: {
    eligible: true,
    checks: { has_arrears: false },
    metrics: { free_credit: '0.40', monthly_free_credit: '0.00' },
  },
};

const FREE_SUB = { success: true, data: { subscription: null } };
const PRO_SUB = (autoRenew: boolean) => ({
  success: true,
  data: {
    subscription: { status: 'ACTIVE', auto_renew: autoRenew, current_period_end: 1790000000 },
  },
});

// Restore window.confirm (and any other) spies so a later test never inherits
// a stale stub. The vi.fn() api mocks are re-primed in beforeEach, and
// mockOpenExternal is fully reset there too, so restoreAllMocks is safe.
afterEach(() => {
  vi.restoreAllMocks();
});

beforeEach(() => {
  mockMode = 'cloud-web';
  mockGetSubscription.mockReset();
  mockGetFeeInfo.mockReset();
  mockGetFeeInfo.mockRejectedValue(new Error('no fee')); // default: balance hidden unless a test opts in
  mockGetRecords.mockReset();
  mockGetRecords.mockRejectedValue(new Error('no records')); // default: activity hidden
  mockGetMyQuota.mockReset();
  mockGetMyQuota.mockResolvedValue({ enabled: false }); // default: no free-tier bar
  mockGetPlans.mockReset();
  mockGetPlans.mockResolvedValue({ success: true, data: { plans: [PRO_PLAN] } });
  mockSetQuotaPreference.mockReset();
  mockSubscribe.mockReset();
  mockCancel.mockReset();
  mockReactivate.mockReset();
  mockRecharge.mockReset();
  mockRechargeStatus.mockReset();
  mockGetProviders.mockReset();
  mockGetProviders.mockResolvedValue(NETMIND_CONNECTED); // default: already connected
  mockOpenExternal.mockReset();
  mockOpenExternal.mockResolvedValue(undefined); // re-prime after restoreAllMocks
});

// Default world for a Free user (fee reject + quota off) classifies as LOW, so
// the top-up controls sit behind the "one-time top-up" link. Helper expands it.
async function openTopUp() {
  fireEvent.click(await screen.findByRole('button', { name: /Just need a one-time top-up/ }));
}

// ── S0 / plan states / error ────────────────────────────────────────────────

test('S0: local mode renders nothing', () => {
  mockMode = 'local';
  const { container } = render(<NetmindAccountPanel />);
  expect(container.firstChild).toBeNull();
});

test('S1: free plan (subscription null) → Free badge, no negative copy', async () => {
  mockGetSubscription.mockResolvedValue(FREE_SUB);
  render(<NetmindAccountPanel />);
  expect(await screen.findByText('Free')).toBeTruthy(); // badge
  expect(screen.queryByText(/not subscribed/)).toBeNull(); // de-negativized
});

test('S2: pro active (auto_renew on) → Pro member status + valid-until', async () => {
  mockGetSubscription.mockResolvedValue(PRO_SUB(true));
  render(<NetmindAccountPanel />);
  expect(await screen.findByText(/Pro member · active/)).toBeTruthy();
  expect(screen.getByText(/Valid until \d{4}-\d{2}-\d{2}/)).toBeTruthy();
});

test('S3: pro cancelled but in-period → downgrade copy + Resume button', async () => {
  mockGetSubscription.mockResolvedValue(PRO_SUB(false));
  render(<NetmindAccountPanel />);
  expect(await screen.findByText(/downgrades to Free/)).toBeTruthy();
  expect(screen.getByRole('button', { name: /Resume auto-renew/ })).toBeTruthy();
});

test('error: fetch rejects -> error copy, no crash', async () => {
  mockGetSubscription.mockRejectedValue(new Error('401'));
  render(<NetmindAccountPanel />);
  await waitFor(() =>
    expect(screen.getByText(/Could not load your NetMind.AI Power account/)).toBeTruthy(),
  );
});

test('footer: scope + sandbox notes shown; charging order moved OUT of footer', async () => {
  mockGetSubscription.mockResolvedValue(FREE_SUB);
  render(<NetmindAccountPanel />);
  expect(await screen.findByText(/cover LLM API usage/)).toBeTruthy();
  expect(screen.getByText(/sandbox itself is free for now/)).toBeTruthy();
  expect(screen.queryByText(/How usage is charged/)).toBeNull();
});

test('header: branded as NetMind.AI Power', async () => {
  mockGetSubscription.mockResolvedValue(FREE_SUB);
  render(<NetmindAccountPanel />);
  expect(await screen.findByText('NetMind.AI Power')).toBeTruthy();
});

// ── plan × runway: free × healthy (progressive disclosure) ─────────────────

test('free × healthy: reassurance shown, ZERO spend buttons, manage link collapsed', async () => {
  mockGetSubscription.mockResolvedValue(FREE_SUB);
  mockGetMyQuota.mockResolvedValue(QUOTA_ACTIVE);
  mockGetFeeInfo.mockResolvedValue(FEE_RICH);
  render(<NetmindAccountPanel />);
  expect(await screen.findByText(/You're all set — running on NetMind/)).toBeTruthy();
  // the core UX goal: no spend CTA anywhere until the user asks
  expect(screen.queryByRole('button', { name: /Subscribe to Pro/ })).toBeNull();
  expect(screen.queryByRole('button', { name: /Upgrade to Pro/ })).toBeNull();
  expect(screen.queryByRole('button', { name: /^Recharge$/ })).toBeNull();
  expect(screen.getByRole('button', { name: /Manage plan & credits/ })).toBeTruthy();
});

test('free × healthy: expanding Manage reveals subscribe + top-up', async () => {
  mockGetSubscription.mockResolvedValue(FREE_SUB);
  mockGetMyQuota.mockResolvedValue(QUOTA_ACTIVE);
  mockGetFeeInfo.mockResolvedValue(FEE_RICH);
  render(<NetmindAccountPanel />);
  fireEvent.click(await screen.findByRole('button', { name: /Manage plan & credits/ }));
  expect(screen.getByRole('button', { name: /Subscribe to Pro/ })).toBeTruthy();
  expect(screen.getByRole('button', { name: /^Recharge$/ })).toBeTruthy();
});

// ── plan × runway: free × low (the decision moment) ────────────────────────

test('free × low: ONE promoted action — upsell card with value prop + dynamic price', async () => {
  mockGetSubscription.mockResolvedValue(FREE_SUB);
  mockGetMyQuota.mockResolvedValue(QUOTA_EXHAUSTED);
  mockGetFeeInfo.mockResolvedValue(FEE_POOR);
  render(<NetmindAccountPanel />);
  expect(await screen.findByText(/Free tier used up. To keep going:/)).toBeTruthy();
  // value prop leads — the ONLY differentiator vs a same-priced top-up
  expect(screen.getByText(/Member pricing on popular models/)).toBeTruthy();
  expect(screen.getByText(/full 100\+ model library/)).toBeTruthy();
  // price/grant pulled from getPlans (monthly_grant_usd=19, period=month→mo)
  expect(screen.getAllByText(/\$19\.00 \/ mo/).length).toBeGreaterThan(0);
  expect(screen.getByRole('button', { name: /Upgrade to Pro/ })).toBeTruthy();
  // top-up demoted to a link, not a peer button
  expect(screen.queryByRole('button', { name: /^Recharge$/ })).toBeNull();
  expect(screen.getByRole('button', { name: /Just need a one-time top-up/ })).toBeTruthy();
});

test('free × low: the one-time top-up link toggles — click again collapses', async () => {
  mockGetSubscription.mockResolvedValue(FREE_SUB);
  render(<NetmindAccountPanel />);
  const link = await screen.findByRole('button', { name: /Just need a one-time top-up/ });
  fireEvent.click(link);
  expect(screen.getByRole('button', { name: /^Recharge$/ })).toBeTruthy();
  fireEvent.click(link); // second click must collapse, not stick open
  expect(screen.queryByRole('button', { name: /^Recharge$/ })).toBeNull();
});

test('free × low: upsell button → api.subscribe + openExternal(checkout_url)', async () => {
  mockGetSubscription.mockResolvedValue(FREE_SUB);
  mockSubscribe.mockResolvedValue({ success: true, data: { checkout_url: 'https://pay/x' } });
  render(<NetmindAccountPanel />);
  fireEvent.click(await screen.findByRole('button', { name: /Upgrade to Pro/ }));
  await waitFor(() => expect(mockSubscribe).toHaveBeenCalled());
  await waitFor(() => expect(mockOpenExternal).toHaveBeenCalledWith('https://pay/x'));
});

test('free × low: plans fetch fails → upsell card still renders, price line hidden', async () => {
  mockGetSubscription.mockResolvedValue(FREE_SUB);
  mockGetPlans.mockRejectedValue(new Error('502'));
  render(<NetmindAccountPanel />);
  expect(await screen.findByRole('button', { name: /Upgrade to Pro/ })).toBeTruthy();
  expect(screen.queryByText(/\$19\.00/)).toBeNull();
});

// ── plan × runway: pro states ───────────────────────────────────────────────

test('pro × healthy: member-pricing note, cancel hidden behind Manage', async () => {
  mockGetSubscription.mockResolvedValue(PRO_SUB(true));
  mockGetFeeInfo.mockResolvedValue(FEE_RICH); // balance $12.50 ≥ buffer → healthy
  render(<NetmindAccountPanel />);
  expect(await screen.findByText(/Pro member · active/)).toBeTruthy();
  expect(screen.getByText(/Member pricing active on popular models/)).toBeTruthy();
  expect(screen.queryByRole('button', { name: /Cancel subscription/ })).toBeNull();
  fireEvent.click(screen.getByRole('button', { name: /Manage subscription & balance/ }));
  expect(screen.getByRole('button', { name: /Cancel subscription/ })).toBeTruthy();
});

test('pro × low: top-up promoted directly (no upsell — already Pro)', async () => {
  mockGetSubscription.mockResolvedValue(PRO_SUB(true));
  mockGetFeeInfo.mockResolvedValue(FEE_POOR); // $0.40 < buffer → low
  render(<NetmindAccountPanel />);
  expect(await screen.findByText(/grant and balance are running low/)).toBeTruthy();
  expect(screen.getByRole('button', { name: /^Recharge$/ })).toBeTruthy(); // no click needed
  expect(screen.queryByRole('button', { name: /Upgrade to Pro/ })).toBeNull();
});

test('S2: cancel via Manage → confirm true → api.cancelSubscription', async () => {
  vi.spyOn(window, 'confirm').mockReturnValue(true);
  mockGetSubscription.mockResolvedValue(PRO_SUB(true));
  mockGetFeeInfo.mockResolvedValue(FEE_RICH);
  mockCancel.mockResolvedValue({ success: true, data: { status: 'auto_renew_off' } });
  render(<NetmindAccountPanel />);
  fireEvent.click(await screen.findByRole('button', { name: /Manage subscription & balance/ }));
  fireEvent.click(screen.getByRole('button', { name: /Cancel subscription/ }));
  await waitFor(() => expect(mockCancel).toHaveBeenCalled());
});

test('S2: cancel confirm dismissed → no api call', async () => {
  vi.spyOn(window, 'confirm').mockReturnValue(false);
  mockGetSubscription.mockResolvedValue(PRO_SUB(true));
  mockGetFeeInfo.mockResolvedValue(FEE_RICH);
  render(<NetmindAccountPanel />);
  fireEvent.click(await screen.findByRole('button', { name: /Manage subscription & balance/ }));
  fireEvent.click(screen.getByRole('button', { name: /Cancel subscription/ }));
  expect(mockCancel).not.toHaveBeenCalled();
});

test('S3: resume button → confirm true → api.reactivateSubscription', async () => {
  vi.spyOn(window, 'confirm').mockReturnValue(true);
  mockGetSubscription.mockResolvedValue(PRO_SUB(false));
  mockReactivate.mockResolvedValue({ success: true, data: { status: 'auto_renew_on' } });
  render(<NetmindAccountPanel />);
  fireEvent.click(await screen.findByRole('button', { name: /Resume auto-renew/ }));
  await waitFor(() => expect(mockReactivate).toHaveBeenCalled());
});

// ── runway view: free-tier bar / balance / grant / flow line / toggle ───────

test('runway: free-tier bar shows the more depleted side (62% left) + tiered flow line', async () => {
  mockGetSubscription.mockResolvedValue(FREE_SUB);
  mockGetMyQuota.mockResolvedValue(QUOTA_ACTIVE);
  mockGetFeeInfo.mockResolvedValue(FEE_RICH);
  render(<NetmindAccountPanel />);
  expect(await screen.findByText('Free tier')).toBeTruthy();
  expect(screen.getByText('62% left')).toBeTruthy();
  // free-tier bar visible → the flow line may mention it
  expect(screen.getByText(/free tier first, then your balance\./)).toBeTruthy();
});

test('runway: no free-tier bar → flow line never claims "free tier first"', async () => {
  // quota feature off → no bar on screen; the copy must not describe a pool
  // the user can't see.
  mockGetSubscription.mockResolvedValue(FREE_SUB);
  mockGetFeeInfo.mockResolvedValue(FEE_RICH);
  render(<NetmindAccountPanel />);
  expect(await screen.findByText('Current balance')).toBeTruthy();
  expect(screen.getByText('$12.50')).toBeTruthy();
  expect(screen.getByText('Usage draws from your balance.')).toBeTruthy();
  expect(screen.queryByText(/free tier first/)).toBeNull();
});

test('runway: grant row is Pro-only; Pro flow line names three pools', async () => {
  // free + rich fee → no grant row
  mockGetSubscription.mockResolvedValue(FREE_SUB);
  mockGetFeeInfo.mockResolvedValue(FEE_RICH);
  const { unmount } = render(<NetmindAccountPanel />);
  await screen.findByText('Current balance');
  expect(screen.queryByText('Monthly grant')).toBeNull();
  unmount();
  // pro + rich fee (no free-tier bar) → grant row + grant-first flow line
  mockGetSubscription.mockResolvedValue(PRO_SUB(true));
  render(<NetmindAccountPanel />);
  expect(await screen.findByText('Monthly grant')).toBeTruthy();
  expect(screen.getByText('$19.00 / mo')).toBeTruthy();
  expect(screen.getByText(/monthly grant first, then your balance\./)).toBeTruthy();
});

test('runway: partial fee payload (no metrics/checks) renders without crashing', async () => {
  mockGetSubscription.mockResolvedValue(FREE_SUB);
  mockGetFeeInfo.mockResolvedValue({ success: true, data: { eligible: false } });
  render(<NetmindAccountPanel />);
  expect(await screen.findByText('NetMind.AI Power')).toBeTruthy();
  // eligible=false → low → the action prompt carries the message; the
  // system-toned "cannot incur paid usage" line is suppressed (redundant).
  expect(await screen.findByText(/To keep going:/)).toBeTruthy();
  expect(screen.queryByText(/Cannot incur paid usage right now/)).toBeNull();
});

test('eligible=false warning still shows for pro_cancelled (no low prompt there)', async () => {
  mockGetSubscription.mockResolvedValue(PRO_SUB(false));
  mockGetFeeInfo.mockResolvedValue({ success: true, data: { eligible: false } });
  render(<NetmindAccountPanel />);
  expect(await screen.findByText(/Cannot incur paid usage right now/)).toBeTruthy();
});

test('runway: hidden when fee fails and quota is off; plan status still shows', async () => {
  mockGetSubscription.mockResolvedValue(FREE_SUB);
  render(<NetmindAccountPanel />);
  await screen.findByRole('button', { name: /Upgrade to Pro/ }); // load settled
  expect(screen.queryByText('Current balance')).toBeNull();
  expect(screen.queryByText('Free tier')).toBeNull();
});

test('runway: quota fetch failure never crashes the panel', async () => {
  mockGetSubscription.mockResolvedValue(FREE_SUB);
  mockGetMyQuota.mockRejectedValue(new Error('500'));
  mockGetFeeInfo.mockResolvedValue(FEE_RICH);
  render(<NetmindAccountPanel />);
  expect(await screen.findByText('Current balance')).toBeTruthy();
  expect(screen.queryByText('Free tier')).toBeNull();
});

// ── prefer toggle (formerly QuotaPanel prefer_system) ──────────────────────

test('prefer toggle: click → setQuotaPreference(false), UI reflects the response', async () => {
  mockGetSubscription.mockResolvedValue(FREE_SUB);
  mockGetMyQuota.mockResolvedValue(QUOTA_ACTIVE); // prefer ON
  mockGetFeeInfo.mockResolvedValue(FEE_RICH);
  mockSetQuotaPreference.mockResolvedValue({ ...QUOTA_ACTIVE, prefer_system_override: false });
  render(<NetmindAccountPanel />);
  const sw = await screen.findByRole('switch');
  expect(sw.getAttribute('aria-checked')).toBe('true');
  fireEvent.click(sw);
  await waitFor(() => expect(mockSetQuotaPreference).toHaveBeenCalledWith(false));
  await waitFor(() => expect(sw.getAttribute('aria-checked')).toBe('false'));
});

test('prefer toggle: exhausted + OFF → locked (cannot turn ON without budget)', async () => {
  mockGetSubscription.mockResolvedValue(FREE_SUB);
  mockGetMyQuota.mockResolvedValue({ ...QUOTA_EXHAUSTED, prefer_system_override: false });
  mockGetFeeInfo.mockResolvedValue(FEE_POOR);
  render(<NetmindAccountPanel />);
  const sw = await screen.findByRole('switch');
  expect((sw as HTMLButtonElement).disabled).toBe(true);
});

test('prefer toggle: rapid double-click fires only ONE api call (sync guard)', async () => {
  mockGetSubscription.mockResolvedValue(FREE_SUB);
  mockGetMyQuota.mockResolvedValue(QUOTA_ACTIVE);
  mockGetFeeInfo.mockResolvedValue(FEE_RICH);
  mockSetQuotaPreference.mockReturnValue(new Promise(() => {})); // in flight forever
  render(<NetmindAccountPanel />);
  const sw = await screen.findByRole('switch');
  fireEvent.click(sw);
  fireEvent.click(sw); // second click lands before the first resolves
  await waitFor(() => expect(mockSetQuotaPreference).toHaveBeenCalledTimes(1));
});

test('free × low with UNKNOWN quota state → neutral copy, not "Free tier used up"', async () => {
  // quota feature off + poor balance → low, but we never observed exhaustion
  mockGetSubscription.mockResolvedValue(FREE_SUB);
  mockGetFeeInfo.mockResolvedValue(FEE_POOR);
  render(<NetmindAccountPanel />);
  expect(await screen.findByText(/You're low on credits. To keep going:/)).toBeTruthy();
  expect(screen.queryByText(/Free tier used up/)).toBeNull();
});

test('prefer toggle: exhausted + ON → still allowed to turn OFF', async () => {
  mockGetSubscription.mockResolvedValue(FREE_SUB);
  mockGetMyQuota.mockResolvedValue(QUOTA_EXHAUSTED); // prefer ON
  mockGetFeeInfo.mockResolvedValue(FEE_POOR);
  mockSetQuotaPreference.mockResolvedValue({ ...QUOTA_EXHAUSTED, prefer_system_override: false });
  render(<NetmindAccountPanel />);
  const sw = await screen.findByRole('switch');
  expect((sw as HTMLButtonElement).disabled).toBe(false);
  fireEvent.click(sw);
  await waitFor(() => expect(mockSetQuotaPreference).toHaveBeenCalledWith(false));
});

// ── module F: read-only connection status ──────────────────────────────────

test('status: netmind provider present → ✓ connected, no connect button', async () => {
  mockGetSubscription.mockResolvedValue(FREE_SUB);
  render(<NetmindAccountPanel />);
  expect(await screen.findByText(/account is connected/)).toBeTruthy();
  expect(screen.queryByRole('button', { name: /Use this account/ })).toBeNull();
});

test('status: no netmind provider → not-connected copy', async () => {
  mockGetSubscription.mockResolvedValue(FREE_SUB);
  mockGetProviders.mockResolvedValue({
    success: true,
    data: { providers: { x: { source: 'user' } }, slots: {} },
  });
  render(<NetmindAccountPanel />);
  expect(await screen.findByText(/isn.t linked as a provider yet/)).toBeTruthy();
});

test('status: not_connected surfaces ABOVE the runway; connected stays below', async () => {
  // not_connected is the only actionable connection state → promoted high
  mockGetSubscription.mockResolvedValue(FREE_SUB);
  mockGetFeeInfo.mockResolvedValue(FEE_RICH);
  mockGetProviders.mockResolvedValue({
    success: true,
    data: { providers: {}, slots: {} },
  });
  const { unmount } = render(<NetmindAccountPanel />);
  const warn = await screen.findByText(/isn.t linked as a provider yet/);
  const balance = screen.getByText('Current balance');
  // warning precedes the balance row in DOM order
  expect(warn.compareDocumentPosition(balance) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  unmount();
  // connected confirmation renders after the balance row (quiet, low)
  mockGetProviders.mockResolvedValue(NETMIND_CONNECTED);
  render(<NetmindAccountPanel />);
  const ok = await screen.findByText(/account is connected/);
  const balance2 = screen.getByText('Current balance');
  expect(balance2.compareDocumentPosition(ok) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
});

test('status: getProviders fails → not-connected (no infinite checking)', async () => {
  mockGetSubscription.mockResolvedValue(FREE_SUB);
  mockGetProviders.mockRejectedValue(new Error('500'));
  render(<NetmindAccountPanel />);
  expect(await screen.findByText(/isn.t linked as a provider yet/)).toBeTruthy();
});

// ── recent activity (settled ledger, collapsed by default) ─────────────────

test('activity: collapsed by default, expands on click', async () => {
  mockGetSubscription.mockResolvedValue(FREE_SUB);
  mockGetRecords.mockResolvedValue({
    success: true,
    data: [
      { record_id: 'r1', kind: 'Recharge', type: 'Recharge', direction: 'income', amount: '10.00', currency: 'USD', status: 'succeeded', created_at: '2026-07-03T03:48:35+00:00' },
    ],
  });
  render(<NetmindAccountPanel />);
  const toggle = await screen.findByRole('button', { name: /Recent activity/ });
  expect(screen.queryByText(/\+\$10\.00 USD/)).toBeNull();
  fireEvent.click(toggle);
  expect(await screen.findByText(/\+\$10\.00 USD/)).toBeTruthy();
});

test('activity: pending records are hidden (abandoned checkouts)', async () => {
  mockGetSubscription.mockResolvedValue(FREE_SUB);
  mockGetRecords.mockResolvedValue({
    success: true,
    data: [
      { record_id: 'p1', kind: 'Recharge', type: 'Recharge', direction: 'income', amount: '10.00', currency: 'USD', status: 'pending', created_at: '2026-07-06T00:00:00+00:00' },
      { record_id: 's1', kind: 'Recharge', type: 'Recharge', direction: 'income', amount: '5.00', currency: 'USD', status: 'succeeded', created_at: '2026-07-05T00:00:00+00:00' },
    ],
  });
  render(<NetmindAccountPanel />);
  fireEvent.click(await screen.findByRole('button', { name: /Recent activity/ }));
  expect(await screen.findByText(/\+\$5\.00 USD/)).toBeTruthy();
  expect(screen.queryByText(/\+\$10\.00 USD/)).toBeNull();
});

test('activity: hidden entirely when every record is pending', async () => {
  mockGetSubscription.mockResolvedValue(FREE_SUB);
  mockGetRecords.mockResolvedValue({
    success: true,
    data: [
      { record_id: 'p1', kind: 'Recharge', type: 'Recharge', direction: 'income', amount: '10.00', currency: 'USD', status: 'pending', created_at: '2026-07-06T00:00:00+00:00' },
    ],
  });
  render(<NetmindAccountPanel />);
  await screen.findByRole('button', { name: /Upgrade to Pro/ });
  expect(screen.queryByText(/Recent activity/)).toBeNull();
});

test('activity: hidden when records fetch fails', async () => {
  mockGetSubscription.mockResolvedValue(FREE_SUB);
  mockGetRecords.mockRejectedValue(new Error('502'));
  render(<NetmindAccountPanel />);
  await screen.findByRole('button', { name: /Upgrade to Pro/ });
  expect(screen.queryByText(/Recent activity/)).toBeNull();
});

// ── recharge / top-up (free × low: expand via the demoted link first) ──────

test('recharge: default tier → api.recharge(10) + openExternal(checkout_url)', async () => {
  mockGetSubscription.mockResolvedValue(FREE_SUB);
  mockRecharge.mockResolvedValue({
    success: true,
    data: { checkout_url: 'https://checkout.stripe.com/x', session_id: 'cs_1' },
  });
  mockRechargeStatus.mockReturnValue(new Promise(() => {})); // stays processing
  render(<NetmindAccountPanel />);
  await openTopUp();
  fireEvent.click(screen.getByRole('button', { name: /^Recharge$/ }));
  await waitFor(() => expect(mockRecharge).toHaveBeenCalledWith(10));
  await waitFor(() =>
    expect(mockOpenExternal).toHaveBeenCalledWith('https://checkout.stripe.com/x'),
  );
});

test('recharge: custom amount overrides the preset tier', async () => {
  mockGetSubscription.mockResolvedValue(FREE_SUB);
  mockRecharge.mockResolvedValue({
    success: true,
    data: { checkout_url: 'https://checkout.stripe.com/x', session_id: 'cs_1' },
  });
  mockRechargeStatus.mockReturnValue(new Promise(() => {}));
  render(<NetmindAccountPanel />);
  await openTopUp();
  fireEvent.change(screen.getByPlaceholderText('Custom'), { target: { value: '25' } });
  fireEvent.click(screen.getByRole('button', { name: /^Recharge$/ }));
  await waitFor(() => expect(mockRecharge).toHaveBeenCalledWith(25));
});

test('recharge: non-positive amount → validation error, no api call', async () => {
  mockGetSubscription.mockResolvedValue(FREE_SUB);
  render(<NetmindAccountPanel />);
  await openTopUp();
  fireEvent.change(screen.getByPlaceholderText('Custom'), { target: { value: '0' } });
  fireEvent.click(screen.getByRole('button', { name: /^Recharge$/ }));
  expect(await screen.findByText(/greater than 0/)).toBeTruthy();
  expect(mockRecharge).not.toHaveBeenCalled();
});

test('recharge: api.recharge rejects → failed state error shown', async () => {
  mockGetSubscription.mockResolvedValue(FREE_SUB);
  mockRecharge.mockRejectedValue(new Error('checkout unavailable'));
  render(<NetmindAccountPanel />);
  await openTopUp();
  fireEvent.click(screen.getByRole('button', { name: /^Recharge$/ }));
  expect(await screen.findByText(/checkout unavailable/)).toBeTruthy();
});

test('recharge: "Stop waiting" leaves processing and allows immediate retry', async () => {
  mockGetSubscription.mockResolvedValue(FREE_SUB);
  mockRecharge.mockResolvedValue({
    success: true,
    data: { checkout_url: 'https://checkout.stripe.com/x', session_id: 'cs_1' },
  });
  mockRechargeStatus.mockReturnValue(new Promise(() => {})); // never resolves → stuck pending
  render(<NetmindAccountPanel />);
  await openTopUp();
  fireEvent.click(screen.getByRole('button', { name: /^Recharge$/ }));
  const stop = await screen.findByRole('button', { name: /Stop waiting/ });
  fireEvent.click(stop);
  await waitFor(() => expect(screen.queryByText(/Waiting for payment/)).toBeNull());
  fireEvent.click(screen.getByRole('button', { name: /^Recharge$/ }));
  await waitFor(() => expect(mockRecharge).toHaveBeenCalledTimes(2));
});
