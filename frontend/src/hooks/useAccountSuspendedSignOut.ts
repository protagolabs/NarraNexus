/**
 * @file_name: useAccountSuspendedSignOut.ts
 * @author: Bin Liang
 * @date: 2026-10-30
 * @description: Sign out when the backend says the account is suspended, so every background poll stops.
 *
 * A suspended account gets 403 `account_suspended` on every request (backend
 * account-state gate). Left alone, each open tab keeps polling into a stream of
 * 403s — the failures the edge monitor bans an IP on, which also takes down
 * everyone else behind that address (an office NAT). api.ts raises
 * `narranexus:account-suspended`; this signs out (polls stop, later requests
 * carry no credential and answer 401, which the edge does not count) and
 * reports it so App can show why. Signing in again is refused by the login
 * gate, so the sign-out is final.
 */
import { useEffect, useState } from 'react';
import { useConfigStore } from '@/stores';

/** Returns whether the "account not available" notice should show, and a dismiss. */
export function useAccountSuspendedSignOut(): [boolean, () => void] {
  const [suspended, setSuspended] = useState(false);
  useEffect(() => {
    const handler = () => {
      const { isLoggedIn, logout } = useConfigStore.getState();
      if (!isLoggedIn) return;
      console.warn('[auth] account not available — signing out to stop requests');
      logout();
      setSuspended(true);
    };
    window.addEventListener('narranexus:account-suspended', handler);
    return () => window.removeEventListener('narranexus:account-suspended', handler);
  }, []);
  return [suspended, () => setSuspended(false)];
}
