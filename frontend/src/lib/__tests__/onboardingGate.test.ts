/**
 * Unit tests for the first-run gate (see lib/onboardingGate.ts).
 *
 * The regression this file exists for: a brand-new account must be sent to the
 * welcome flow. Login provisions a guide agent (and sometimes NetMind provider
 * cards) for that account, and the first version of the gate read those as
 * "this user is already set up" — so every new user landed straight in the chat
 * and had the flag written behind their back (Owner report 2026-08-27).
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';

const mockGetOnboarding = vi.fn();
const mockGetAgents = vi.fn();
const mockGetProviders = vi.fn();
const mockMarkStep = vi.fn();
vi.mock('@/lib/api', () => ({
  api: {
    getOnboarding: (...a: unknown[]) => mockGetOnboarding(...a),
    getAgents: (...a: unknown[]) => mockGetAgents(...a),
    getProviders: (...a: unknown[]) => mockGetProviders(...a),
    markOnboardingStep: (...a: unknown[]) => mockMarkStep(...a),
  },
}));

const guideOf = (uid: string) => ({ agent_id: 'agt_guide', name: 'Daring_Cinder_Mantis', bootstrap_active: true, created_by: uid });
const ownOf = (uid: string) => ({ agent_id: 'agt_mine', name: 'my agent', created_by: uid });
// The agent list also carries every public agent on the platform, including
// strangers' agents that are themselves still bootstrap-active.
const strangerPublic = { agent_id: 'agt_public', name: 'someone else', created_by: 'u_stranger', bootstrap_active: true };

/** Fresh module per test — the gate caches answers in module scope. */
async function loadGate() {
  vi.resetModules();
  return await import('../onboardingGate');
}

beforeEach(() => {
  vi.clearAllMocks();
  mockGetOnboarding.mockResolvedValue({ success: true, progress: { landing_completed: false } });
  mockGetAgents.mockResolvedValue({ agents: [] });
  mockGetProviders.mockResolvedValue({ success: true, data: { providers: {} } });
  mockMarkStep.mockResolvedValue({ success: true });
});

describe('owesWelcomeFlow', () => {
  it('lets a user who already finished the flow straight through', async () => {
    mockGetOnboarding.mockResolvedValue({ success: true, progress: { landing_completed: true } });
    const { owesWelcomeFlow } = await loadGate();
    expect(await owesWelcomeFlow('u1')).toBe(false);
    expect(mockGetAgents).not.toHaveBeenCalled(); // no need to classify
  });

  it('still owes the flow when login only auto-provisioned a guide agent', async () => {
    mockGetAgents.mockResolvedValue({ agents: [guideOf('u_new')] });
    mockGetProviders.mockResolvedValue({
      success: true,
      data: {
        providers: {
          p1: { source: 'netmind', auto_provisioned: true },
          p2: { source: 'netmind_free', auto_provisioned: true },
        },
      },
    });
    const { owesWelcomeFlow } = await loadGate();
    expect(await owesWelcomeFlow('u_new')).toBe(true);
    // and it must NOT quietly mark the flow as seen
    expect(mockMarkStep).not.toHaveBeenCalled();
  });

  it('backfills an existing user who has agents of their own', async () => {
    mockGetAgents.mockResolvedValue({ agents: [guideOf('u_old'), ownOf('u_old')] });
    const { owesWelcomeFlow } = await loadGate();
    expect(await owesWelcomeFlow('u_old')).toBe(false);
    expect(mockMarkStep).toHaveBeenCalledWith('u_old', 'landing_completed');
  });

  it("strangers' public agents neither count as the user's own nor as their guide", async () => {
    mockGetAgents.mockResolvedValue({ agents: [strangerPublic, { ...ownOf('u_stranger') }] });
    const { owesWelcomeFlow } = await loadGate();
    expect(await owesWelcomeFlow('u_new')).toBe(true);
    expect(mockMarkStep).not.toHaveBeenCalled();
  });

  it('backfills an existing user who wired their own provider', async () => {
    mockGetProviders.mockResolvedValue({
      success: true,
      data: { providers: { p1: { source: 'codex_oauth', auto_provisioned: false } } },
    });
    const { owesWelcomeFlow } = await loadGate();
    expect(await owesWelcomeFlow('u_old2')).toBe(false);
    expect(mockMarkStep).toHaveBeenCalledWith('u_old2', 'landing_completed');
  });

  it('trusts the server flag, not the source name — a new auto card must not age an account', async () => {
    // The backend decides which cards login made. A source this file has never
    // heard of, flagged auto_provisioned, still does not count as the user's.
    mockGetAgents.mockResolvedValue({ agents: [guideOf('u_new2')] });
    mockGetProviders.mockResolvedValue({
      success: true,
      data: { providers: { p9: { source: 'some_new_wallet', auto_provisioned: true } } },
    });
    const { owesWelcomeFlow } = await loadGate();
    expect(await owesWelcomeFlow('u_new2')).toBe(true);
    expect(mockMarkStep).not.toHaveBeenCalled();
  });

  it('asks once per user, however many routes mount', async () => {
    mockGetAgents.mockResolvedValue({ agents: [guideOf('u_new')] });
    const { owesWelcomeFlow } = await loadGate();
    const [a, b, c] = await Promise.all([
      owesWelcomeFlow('u_new'),
      owesWelcomeFlow('u_new'),
      owesWelcomeFlow('u_new'),
    ]);
    expect([a, b, c]).toEqual([true, true, true]);
    expect(mockGetOnboarding).toHaveBeenCalledTimes(1);
    // and again after it settled — still cached
    expect(await owesWelcomeFlow('u_new')).toBe(true);
    expect(mockGetOnboarding).toHaveBeenCalledTimes(1);
  });

  it('markWelcomeSeen flips the answer without another request', async () => {
    mockGetAgents.mockResolvedValue({ agents: [guideOf('u_new')] });
    const { owesWelcomeFlow, markWelcomeSeen } = await loadGate();
    expect(await owesWelcomeFlow('u_new')).toBe(true);
    markWelcomeSeen('u_new');
    expect(await owesWelcomeFlow('u_new')).toBe(false);
    expect(mockGetOnboarding).toHaveBeenCalledTimes(1);
  });

  it('never blocks the app when the backend is unreachable', async () => {
    mockGetOnboarding.mockRejectedValue(new Error('backend down'));
    const { owesWelcomeFlow } = await loadGate();
    expect(await owesWelcomeFlow('u_x')).toBe(false);
  });

  it('drops cached answers on a session wipe', async () => {
    mockGetAgents.mockResolvedValue({ agents: [guideOf('u_new')] });
    const { owesWelcomeFlow, clearOnboardingGateCache } = await loadGate();
    expect(await owesWelcomeFlow('u_new')).toBe(true);
    clearOnboardingGateCache();
    expect(await owesWelcomeFlow('u_new')).toBe(true);
    expect(mockGetOnboarding).toHaveBeenCalledTimes(2);
  });
});
