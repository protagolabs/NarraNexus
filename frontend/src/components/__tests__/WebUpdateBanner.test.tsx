/**
 * @file_name: WebUpdateBanner.test.tsx
 * @description: The cloud "new version" pill: shown on a new deployed build, cloud web only.
 */
import { act, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

let reportNewBuild: ((id: string) => void) | null = null;
const startBuildWatch = vi.fn((onNew: (id: string) => void) => {
  reportNewBuild = onNew;
  return () => {};
});
vi.mock('@/lib/buildWatch', () => ({ startBuildWatch: (cb: (id: string) => void) => startBuildWatch(cb) }));

const forcedCloud = { value: true };
vi.mock('@/lib/runtimeConfig', () => ({ isForcedCloud: () => forcedCloud.value }));
const tauri = { value: false };
vi.mock('@/lib/tauri', () => ({ isTauri: () => tauri.value }));
vi.mock('react-i18next', () => ({ useTranslation: () => ({ t: (k: string) => k }) }));

import WebUpdateBanner from '../WebUpdateBanner';

const originalLocation = window.location;

afterEach(() => {
  // The reload test swaps window.location; later tests must see the real one.
  Object.defineProperty(window, 'location', { configurable: true, value: originalLocation });
});

beforeEach(() => {
  reportNewBuild = null;
  startBuildWatch.mockClear();
  forcedCloud.value = true;
  tauri.value = false;
});

describe('WebUpdateBanner', () => {
  it('appears once a new build is reported, and reloads on click', () => {
    const reload = vi.fn();
    Object.defineProperty(window, 'location', { configurable: true, value: { ...window.location, reload } });
    render(<WebUpdateBanner />);
    expect(screen.queryByText('appBanners.newWebVersion')).toBeNull();
    act(() => reportNewBuild?.('new'));
    expect(screen.getByText('appBanners.newWebVersion')).toBeTruthy();
    fireEvent.click(screen.getByText('appBanners.reloadNow'));
    expect(reload).toHaveBeenCalledTimes(1);
  });

  it('can be dismissed for this tab', () => {
    render(<WebUpdateBanner />);
    act(() => reportNewBuild?.('new'));
    fireEvent.click(screen.getByLabelText('appBanners.dismiss'));
    expect(screen.queryByText('appBanners.newWebVersion')).toBeNull();
  });

  it('does not watch outside forced-cloud web (desktop / local)', () => {
    forcedCloud.value = false;
    render(<WebUpdateBanner />);
    tauri.value = true;
    forcedCloud.value = true;
    render(<WebUpdateBanner />);
    expect(startBuildWatch).not.toHaveBeenCalled();
  });
});
