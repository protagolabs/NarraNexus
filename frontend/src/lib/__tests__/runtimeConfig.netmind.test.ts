import { afterEach, describe, expect, test, vi } from 'vitest';
import { getNetmindConfig, isPowerLoginAvailable } from '../runtimeConfig';

declare global {
  interface Window { __NARRANEXUS_CONFIG__?: Record<string, unknown>; }
}

afterEach(() => {
  delete window.__NARRANEXUS_CONFIG__;
  vi.unstubAllEnvs();
});

describe('getNetmindConfig', () => {
  test('injected NetMind keys win over dev defaults', () => {
    window.__NARRANEXUS_CONFIG__ = {
      netmindAuthApi: 'https://userauth.protago-dev.com/',
      netmindAccountsUrl: 'https://accounts.protago-dev.com',
      netmindSysCode: 'f925fc2c',
      netmindRegisterUrl: 'https://example.test/register',
    };
    const c = getNetmindConfig();
    expect(c.authApi).toBe('https://userauth.protago-dev.com'); // trailing slash stripped
    expect(c.accountsUrl).toBe('https://accounts.protago-dev.com');
    expect(c.sysCode).toBe('f925fc2c');
    expect(c.registerUrl).toBe('https://example.test/register');
  });

  test('vite dev server falls back to protago-dev defaults when nothing is set', () => {
    // `npm run dev` without any VITE_NETMIND_* (scripts/dev/netmind_env.sh
    // exports them explicitly for run.sh) still reaches the dev environment.
    vi.stubEnv('DEV', true);
    const c = getNetmindConfig();
    expect(c.authApi).toBe('https://userauth.protago-dev.com');
    expect(c.accountsUrl).toBe('https://accounts.protago-dev.com');
    expect(c.sysCode).toBe('f925fc2c');
  });

  test('a production bundle never falls back to protago-dev', () => {
    // B-40: `vite build` output (desktop DMG, cloud image) with an endpoint
    // left unset must land on PROD NetMind, not the dev OAuth app.
    vi.stubEnv('DEV', false);
    const c = getNetmindConfig();
    expect(c.authApi).toBe('https://auth-api.netmind.ai');
    expect(c.accountsUrl).toBe('https://accounts.netmind.ai');
    expect(c.sysCode).toBe('f925fc2c');
    expect(c.registerUrl).toBe('https://www.netmind.ai/sign/register');
    expect(JSON.stringify(c)).not.toContain('protago-dev');
  });

  test('VITE_* build env beats the compiled-in fallback', () => {
    vi.stubEnv('DEV', true);
    vi.stubEnv('VITE_NETMIND_AUTH_API', 'https://auth-api.netmind.ai/');
    vi.stubEnv('VITE_NETMIND_ACCOUNTS_URL', 'https://accounts.netmind.ai');
    const c = getNetmindConfig();
    expect(c.authApi).toBe('https://auth-api.netmind.ai');
    expect(c.accountsUrl).toBe('https://accounts.netmind.ai');
  });
});

describe('isPowerLoginAvailable', () => {
  test('false in a plain local build with no opt-in and no injected endpoints', () => {
    // No forced-cloud, no VITE_ENABLE_POWER_LOGIN, no injected netmindAuthApi.
    expect(isPowerLoginAvailable()).toBe(false);
  });

  test('true when the deploy forces cloud mode', () => {
    window.__NARRANEXUS_CONFIG__ = { mode: 'cloud' };
    expect(isPowerLoginAvailable()).toBe(true);
  });

  test('true when /config.js injects NetMind endpoints (local-mode power deploy)', () => {
    window.__NARRANEXUS_CONFIG__ = {
      mode: 'local',
      netmindAuthApi: 'https://userauth.protago-dev.com',
    };
    expect(isPowerLoginAvailable()).toBe(true);
  });
});
