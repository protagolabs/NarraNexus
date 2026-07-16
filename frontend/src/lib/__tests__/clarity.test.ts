/**
 * @file_name: clarity.test.ts
 * @description: Pin cloud-only loading of the Microsoft Clarity snippet.
 *
 * initClarity() must be a strict no-op on desktop (Tauri) and local
 * self-host builds — isForcedCloud() is false there. Only forced-cloud
 * deploys (agent.narra.nexus) should ever see a network request to
 * clarity.ms.
 */
import { afterEach, beforeEach, describe, expect, test, vi } from 'vitest';

const isForcedCloudMock = vi.fn(() => false);

vi.mock('@/lib/runtimeConfig', () => ({
  isForcedCloud: () => isForcedCloudMock(),
}));

import { initClarity } from '../clarity';

function clearInjectedScripts(): void {
  document.head
    .querySelectorAll('script[data-clarity-project-id]')
    .forEach((el) => el.remove());
}

beforeEach(() => {
  isForcedCloudMock.mockReset();
  isForcedCloudMock.mockReturnValue(false);
  clearInjectedScripts();
});

afterEach(() => {
  clearInjectedScripts();
});

describe('initClarity', () => {
  test('does NOT inject a script when not forced-cloud (desktop / local)', () => {
    isForcedCloudMock.mockReturnValue(false);
    initClarity();
    expect(document.head.querySelector('script[data-clarity-project-id]')).toBeNull();
  });

  test('injects the Clarity snippet with the project id when forced-cloud', () => {
    isForcedCloudMock.mockReturnValue(true);
    initClarity();
    const script = document.head.querySelector('script[data-clarity-project-id]');
    expect(script).not.toBeNull();
    expect(script?.getAttribute('data-clarity-project-id')).toBe('xnaag1qmu0');
    expect(script?.textContent).toContain('xnaag1qmu0');
  });

  test('is idempotent — calling twice only inserts one script tag', () => {
    isForcedCloudMock.mockReturnValue(true);
    initClarity();
    initClarity();
    const scripts = document.head.querySelectorAll('script[data-clarity-project-id]');
    expect(scripts.length).toBe(1);
  });
});
