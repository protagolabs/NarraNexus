/**
 * @file_name: UpdateBanner.tsx
 * @description: Global ChatGPT-style update banner.
 *
 * Surfaces the unified updater state machine (see stores/updaterStore +
 * Rust commands/updater.rs). Renders ONLY at state.kind === 'ready' so
 * the user is never interrupted while the pipeline is silently working
 * in the background (download + install run end-to-end before any UI
 * shows up). On Ready: small floating pill at top-center, dismissable,
 * one click to restart.
 *
 * Why not show 'available' / 'downloading' / 'failed' here too:
 *  - 'available' is transient (microseconds before Downloading starts).
 *  - 'downloading' & 'installing' run silently per Owner spec (Q1=A,
 *    background download). Users who want detail open Settings.
 *  - 'failed' is shown in Settings only — a hard error banner every
 *    startup over a network blip would train users to dismiss without
 *    reading.
 *
 * Auto-mount from `App.tsx`; the store's `init()` (called on App mount)
 * wires the live `updater:state` listener.
 */

import { useState } from 'react';
import { Download } from 'lucide-react';
import { FloatingPill } from '@/components/FloatingPill';
import { useUpdaterStore } from '@/stores/updaterStore';
import { restartForUpdate } from '@/lib/tauri';

export default function UpdateBanner() {
  const state = useUpdaterStore((s) => s.state);
  // Session-scoped dismiss. Re-shows on next time state.kind transitions
  // back to 'ready' (e.g. user dismissed, then a newer build dropped
  // and finished downloading later). We compare on (kind, version) so a
  // genuinely-new Ready re-arms the banner even if `version` differs.
  const [dismissedKey, setDismissedKey] = useState<string | null>(null);

  if (state.kind !== 'ready') return null;
  const key = `ready:${state.version}`;
  if (dismissedKey === key) return null;

  return (
    <FloatingPill
      icon={Download}
      actionLabel="Restart now"
      onAction={() => restartForUpdate()}
      onDismiss={() => setDismissedKey(key)}
      dismissLabel="Dismiss update notification"
      dismissTitle="Dismiss (we'll wait — restart later from Settings)"
    >
      NarraNexus <b>{state.version}</b> downloaded and ready
    </FloatingPill>
  );
}
